"""
Iron ball chained to the cursor — Windows desktop toy (v2).

Improvements over v1:
  * True per-pixel alpha via UpdateLayeredWindow (no color-key artifacts; smooth AA).
  * Multi-node chain: 14 point masses connected by distance constraints
    (Position-Based Dynamics). Cursor and ball are nodes in the same solver, so
    cursor drag emerges naturally from the mass ratio.
  * Procedurally rendered iron ball: per-pixel sphere normals, Phong lighting,
    fresnel rim, surface micro-noise, anti-aliased edge.
  * Procedurally rendered chain link with cross-section shading.

Hold LEFT CTRL to free the cursor and show a repositioning crosshair.
Press CTRL+SHIFT+Q to quit.
"""

import math
import os
import random
import sys
import ctypes
from ctypes import wintypes, byref, sizeof, c_void_p, Structure, POINTER

import numpy as np
import pygame
import win32api
import win32con
import win32gui


# ---------- Tunables ----------
CHAIN_TOTAL_LEN     = 170.0
NUM_LINKS           = 14            # chain segments (15 nodes incl. cursor + ball)
LINK_LEN            = CHAIN_TOTAL_LEN / NUM_LINKS
BALL_RADIUS         = 32
BALL_MASS           = 12.0
BALL_SPIN_RESPONSE  = 0.85          # visual roll amount from ball velocity
LINK_MASS           = 0.22
CURSOR_MASS_BASE    = 1.0           # cursor mass when ball is at rest
CURSOR_SPEED_REF    = 5.5           # px/substep; ball at this speed halves cursor mass
CURSOR_MIN_MASS     = 0.12          # lower bound — keeps things from going singular
CURSOR_PULL_GAIN    = 0.75          # amplifies real solver tension on the cursor
CURSOR_PULL_MAX     = 20.0          # max cursor tug per substep in pixels
BALL_CURSOR_DRAG    = 0.34          # direct momentum transfer when chain is taut
GRAVITY             = 0.85
AIR_DAMP_PER_SEC    = 0.82          # fraction of velocity that survives 1 second of air
GROUND_BOUNCE       = 0.1
GROUND_FRICTION     = 0.94
WALL_BOUNCE         = 0.55
CONSTRAINT_ITERS    = 14
SUBSTEPS            = 2
FPS                 = 100
DROP_IMPACT_MIN     = 3.2
DROP_SOUND_COOLDOWN = 130           # milliseconds
CHAIN_SOUND_MIN     = 0.65
CHAIN_SOUND_COOLDOWN = 75           # milliseconds

# Per-substep damping derived so total per-second damping equals AIR_DAMP_PER_SEC.
AIR_DAMP            = AIR_DAMP_PER_SEC ** (1.0 / (FPS * SUBSTEPS))

# Off-screen frame size — must comfortably contain the chain + ball + crosshair.
# Chain reaches ~CHAIN_TOTAL_LEN; ball pad = BALL_RADIUS; crosshair pad ~60.
FRAME_W             = 384
FRAME_H             = 384

QUIT_VKS            = (win32con.VK_CONTROL, win32con.VK_SHIFT, ord('Q'))
VK_LCONTROL         = 0xA2
VK_RCONTROL         = 0xA3


# =====================================================================
# Win32 layered-window plumbing (per-pixel alpha)
# =====================================================================

user32 = ctypes.windll.user32
gdi32  = ctypes.windll.gdi32

# LRESULT / LONG_PTR is pointer-sized on x64 — c_ssize_t matches.
LRESULT = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]
user32.DefWindowProcW.restype = LRESULT
user32.DispatchMessageW.argtypes = [POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype  = LRESULT

WS_EX_LAYERED       = 0x00080000
WS_EX_TRANSPARENT   = 0x00000020
WS_EX_TOPMOST       = 0x00000008
WS_EX_TOOLWINDOW    = 0x00000080
WS_EX_NOACTIVATE    = 0x08000000
WS_POPUP            = 0x80000000
SW_SHOW             = 5
ULW_ALPHA           = 0x00000002
AC_SRC_OVER         = 0x00
AC_SRC_ALPHA        = 0x01
BI_RGB              = 0


class BITMAPINFOHEADER(Structure):
    _fields_ = [
        ("biSize",          wintypes.DWORD),
        ("biWidth",         wintypes.LONG),
        ("biHeight",        wintypes.LONG),
        ("biPlanes",        wintypes.WORD),
        ("biBitCount",      wintypes.WORD),
        ("biCompression",   wintypes.DWORD),
        ("biSizeImage",     wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed",       wintypes.DWORD),
        ("biClrImportant",  wintypes.DWORD),
    ]


class BITMAPINFO(Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class POINT(Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class SIZE(Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class BLENDFUNCTION(Structure):
    _fields_ = [
        ("BlendOp",             ctypes.c_byte),
        ("BlendFlags",          ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat",         ctypes.c_byte),
    ]


class LayeredOverlay:
    """A topmost, click-through, per-pixel-alpha window the size of the screen."""

    _next_id = 0

    def __init__(self, width: int, height: int):
        self.w = width
        self.h = height
        self.class_name = f"BallChainOverlayCls{LayeredOverlay._next_id}"
        LayeredOverlay._next_id += 1

        WNDPROC = ctypes.WINFUNCTYPE(
            LRESULT, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM,
        )

        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == win32con.WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = WNDPROC(wnd_proc)

        wc = win32gui.WNDCLASS()
        wc.lpszClassName = self.class_name
        wc.lpfnWndProc   = self._wndproc
        wc.hInstance     = win32api.GetModuleHandle(None)
        wc.hbrBackground = 0
        wc.style         = 0
        self.class_atom  = win32gui.RegisterClass(wc)

        self.hwnd = win32gui.CreateWindowEx(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST
            | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            self.class_atom,
            self.class_name,
            WS_POPUP,
            0, 0, width, height,
            0, 0, wc.hInstance, None,
        )
        win32gui.ShowWindow(self.hwnd, SW_SHOW)

        # Memory DC + 32-bit DIB section we'll write into each frame.
        self.hdc_screen = user32.GetDC(0)
        self.hdc_mem    = gdi32.CreateCompatibleDC(self.hdc_screen)

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize        = sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth       = width
        bmi.bmiHeader.biHeight      = -height          # top-down
        bmi.bmiHeader.biPlanes      = 1
        bmi.bmiHeader.biBitCount    = 32
        bmi.bmiHeader.biCompression = BI_RGB

        self.p_bits = c_void_p()
        self.hbmp = gdi32.CreateDIBSection(
            self.hdc_mem, byref(bmi), 0, byref(self.p_bits), 0, 0,
        )
        self.old_obj = gdi32.SelectObject(self.hdc_mem, self.hbmp)

        self._byte_count = width * height * 4

    def push(self, rgba_premult_bgra_bytes: bytes, dst_x: int, dst_y: int) -> None:
        """Upload a frame (BGRA premultiplied, top-down) and refresh the window
        at screen position (dst_x, dst_y). The window resizes/moves atomically."""
        ctypes.memmove(self.p_bits, rgba_premult_bgra_bytes, self._byte_count)
        pt_src = POINT(0, 0)
        pt_dst = POINT(dst_x, dst_y)
        sz     = SIZE(self.w, self.h)
        blend  = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        user32.UpdateLayeredWindow(
            self.hwnd, self.hdc_screen, byref(pt_dst), byref(sz),
            self.hdc_mem, byref(pt_src), 0, byref(blend), ULW_ALPHA,
        )

    def pump(self) -> bool:
        """Drain the message queue. Returns False if WM_QUIT received."""
        msg = wintypes.MSG()
        while user32.PeekMessageW(byref(msg), 0, 0, 0, 1):  # PM_REMOVE
            if msg.message == 0x0012:                      # WM_QUIT
                return False
            user32.TranslateMessage(byref(msg))
            user32.DispatchMessageW(byref(msg))
        return True

    def close(self):
        gdi32.SelectObject(self.hdc_mem, self.old_obj)
        gdi32.DeleteObject(self.hbmp)
        gdi32.DeleteDC(self.hdc_mem)
        user32.ReleaseDC(0, self.hdc_screen)
        win32gui.DestroyWindow(self.hwnd)


# =====================================================================
# Procedural texture rendering
# =====================================================================

LIGHT_DIR = np.array([-0.45, -0.55, 0.70])
LIGHT_DIR /= np.linalg.norm(LIGHT_DIR)


class IronBallRenderer:
    """Per-frame sphere renderer with fixed world lighting and rotating metal."""

    def __init__(self, radius: int):
        self.radius = radius
        self.pad = 4
        self.size = radius * 2 + self.pad * 2
        c = (self.size - 1) / 2.0

        yy, xx = np.mgrid[0:self.size, 0:self.size].astype(np.float32)
        self.dx = xx - c
        self.dy = yy - c
        r2 = self.dx * self.dx + self.dy * self.dy
        r = np.sqrt(r2)

        self.nz = np.sqrt(np.maximum(0.0, radius * radius - r2)) / radius
        self.nx = self.dx / radius
        self.ny = self.dy / radius
        self.alpha = (np.clip(radius - r + 0.5, 0.0, 1.0) * 255.0).astype(np.uint8)

        Lx, Ly, Lz = LIGHT_DIR
        ndotl = np.clip(self.nx * Lx + self.ny * Ly + self.nz * Lz, 0.0, 1.0)
        rx = 2.0 * ndotl * self.nx - Lx
        ry = 2.0 * ndotl * self.ny - Ly
        rz = 2.0 * ndotl * self.nz - Lz

        self.shade = 0.18 + 0.68 * ndotl
        self.spec_soft = np.power(np.clip(rz, 0.0, 1.0), 48.0)
        self.spec_hard = np.power(np.clip(rz, 0.0, 1.0), 180.0)

        fresnel = np.power(1.0 - self.nz, 3.0)
        bottom = np.clip((self.dy / radius) - 0.45, 0.0, 1.0)
        self.edge_falloff = (1.0 - 0.48 * fresnel) * (1.0 - 0.36 * bottom)

        self.texture = self._build_surface_texture(192, 96)

    @staticmethod
    def _smooth_noise(rng, h: int, w: int, passes: int = 5):
        noise = rng.standard_normal((h, w)).astype(np.float32)
        for _ in range(passes):
            noise = (noise
                     + np.roll(noise, 1, 0) + np.roll(noise, -1, 0)
                     + np.roll(noise, 1, 1) + np.roll(noise, -1, 1)) / 5.0
        return noise

    def _build_surface_texture(self, w: int, h: int) -> np.ndarray:
        rng = np.random.default_rng(23)
        v, u = np.mgrid[0:h, 0:w].astype(np.float32)

        cloud = self._smooth_noise(rng, h, w, passes=7)
        grain = rng.standard_normal((h, w)).astype(np.float32)
        brushed = np.sin(u * 0.35 + 0.7 * np.sin(v * 0.18)) * 5.0
        belt = np.sin((v / h) * math.pi * 9.0 + cloud * 0.9) * 4.0

        scratches = np.zeros((h, w), dtype=np.float32)
        for _ in range(44):
            y = rng.integers(2, h - 2)
            x0 = rng.integers(0, w)
            length = rng.integers(18, 70)
            strength = rng.uniform(5.0, 18.0)
            slope = rng.uniform(-0.10, 0.10)
            for x in range(length):
                xx = (x0 + x) % w
                yy = int(np.clip(y + slope * x, 0, h - 1))
                scratches[yy, xx] += strength
                if yy + 1 < h:
                    scratches[yy + 1, xx] += strength * 0.28

        dents = np.zeros((h, w), dtype=np.float32)
        for _ in range(18):
            cx = rng.uniform(0, w)
            cy = rng.uniform(0, h)
            rad = rng.uniform(2.0, 8.0)
            du = np.minimum(np.abs(u - cx), w - np.abs(u - cx))
            dv = v - cy
            dents -= np.exp(-(du * du + dv * dv) / (2.0 * rad * rad)) * rng.uniform(6.0, 18.0)

        value = 58.0 + cloud * 18.0 + grain * 3.0 + brushed + belt + scratches + dents
        tint = np.dstack([
            value * 0.88,
            value * 0.91,
            value * 1.02,
        ])
        return np.clip(tint, 22.0, 128.0).astype(np.float32)

    def _sample_texture(self, ox, oy, oz) -> np.ndarray:
        tex = self.texture
        h, w = tex.shape[:2]

        lon = np.arctan2(oz, ox)
        lat = np.arcsin(np.clip(oy, -1.0, 1.0))
        u = ((lon / (math.pi * 2.0)) + 0.5) * w
        v = (0.5 - lat / math.pi) * (h - 1)

        x0 = np.floor(u).astype(np.int32) % w
        y0 = np.clip(np.floor(v).astype(np.int32), 0, h - 1)
        x1 = (x0 + 1) % w
        y1 = np.clip(y0 + 1, 0, h - 1)
        tx = (u - np.floor(u))[..., None]
        ty = (v - np.floor(v))[..., None]

        top = tex[y0, x0] * (1.0 - tx) + tex[y0, x1] * tx
        bottom = tex[y1, x0] * (1.0 - tx) + tex[y1, x1] * tx
        return top * (1.0 - ty) + bottom * ty

    def render(self, yaw: float, pitch: float) -> pygame.Surface:
        sy, cy = math.sin(-yaw), math.cos(-yaw)
        sp, cp = math.sin(-pitch), math.cos(-pitch)

        # Transform visible world normals back into object space. The light stays
        # fixed, but scratches/dents move across the sphere as the object rotates.
        x1 = cy * self.nx + sy * self.nz
        z1 = -sy * self.nx + cy * self.nz
        y1 = self.ny

        ox = x1
        oy = cp * y1 - sp * z1
        oz = sp * y1 + cp * z1

        base = self._sample_texture(ox, oy, oz)
        spec = (self.spec_soft[..., None] * np.array([110, 120, 145], dtype=np.float32)
                + self.spec_hard[..., None] * np.array([245, 250, 255], dtype=np.float32))
        rim_blue = np.power(1.0 - self.nz, 4.0)[..., None] * np.array([12, 16, 26], dtype=np.float32)

        rgb = base * (self.shade * self.edge_falloff)[..., None] + spec + rim_blue
        rgb = np.clip(rgb, 0, 255).astype(np.uint8)

        rgba = np.dstack([rgb, self.alpha])
        h, w = rgba.shape[:2]
        return pygame.image.frombuffer(rgba.tobytes(), (w, h), 'RGBA').copy()


def bake_chain_link(length: float = 22.0, ring_thickness: float = 3.0,
                    ring_radius: float = 4.5) -> pygame.Surface:
    """Stadium-shaped (capsule) chain link with cross-section shading."""
    pad = 3
    half_len  = length / 2.0
    inner_len = max(0.0, half_len - ring_radius - ring_thickness)
    outer_w   = ring_radius + ring_thickness
    w = int(math.ceil(length)) + pad * 2
    h = int(math.ceil((ring_radius + ring_thickness) * 2)) + pad * 2

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx = (w - 1) / 2.0
    cy = (h - 1) / 2.0
    dx = xx - cx
    dy = yy - cy

    # Distance to capsule centerline
    dx_c = np.clip(dx, -inner_len, inner_len)
    line_dist = np.sqrt((dx - dx_c) ** 2 + dy ** 2)

    # Distance to ring centerline (signed: + outside, - inside)
    edge = line_dist - ring_radius
    abs_edge = np.abs(edge)

    # Ring cross-section: circle of radius ring_thickness
    rho = np.clip(edge / ring_thickness, -1.0, 1.0)
    nz_local = np.sqrt(np.maximum(0.0, 1.0 - rho * rho))

    # Outward direction in 2D plane (perpendicular to the centerline tangent)
    out_x = (dx - dx_c)
    out_y = dy
    out_n = np.sqrt(out_x ** 2 + out_y ** 2) + 1e-6
    out_x /= out_n
    out_y /= out_n

    nx = out_x * rho
    ny = out_y * rho
    nz = nz_local

    Lx, Ly, Lz = LIGHT_DIR
    NdotL = np.clip(nx * Lx + ny * Ly + nz * Lz, 0.0, 1.0)
    Rx = 2.0 * NdotL * nx - Lx
    Ry = 2.0 * NdotL * ny - Ly
    Rz = 2.0 * NdotL * nz - Lz
    spec = np.power(np.clip(Rz, 0.0, 1.0), 35.0)

    base = np.array([78.0, 80.0, 88.0], dtype=np.float32)
    shade = 0.22 + 0.55 * NdotL

    rgb = (base[None, None, :] * shade[..., None]
           + spec[..., None] * np.array([210, 215, 230], dtype=np.float32))
    rgb = np.clip(rgb, 0, 255)

    # Anti-aliased alpha: distance from ring surface
    alpha = np.clip(ring_thickness - abs_edge + 0.5, 0.0, 1.0) * 255.0

    rgba = np.dstack([rgb, alpha]).astype(np.uint8)
    surf = pygame.image.frombuffer(rgba.tobytes(), (w, h), 'RGBA').copy()
    return surf


# =====================================================================
# Chain physics (Position-Based Dynamics)
# =====================================================================

class Node:
    __slots__ = ('x', 'y', 'px', 'py', 'inv_mass')

    def __init__(self, x, y, mass):
        self.x = self.px = float(x)
        self.y = self.py = float(y)
        self.inv_mass = 0.0 if mass == float('inf') else 1.0 / mass


def build_chain(cx: int, cy: int):
    """Cursor (node 0) → chain links → ball (node N). Initially hanging straight down."""
    nodes = []
    for i in range(NUM_LINKS + 1):
        t = i / NUM_LINKS
        x = cx
        y = cy + CHAIN_TOTAL_LEN * t
        if i == 0:
            mass = float('inf')   # set per-frame; placeholder
        elif i == NUM_LINKS:
            mass = BALL_MASS
        else:
            mass = LINK_MASS
        nodes.append(Node(x, y, mass))
    return nodes


def physics_step(nodes, sys_cx, sys_cy, ctrl_held, screen_w, screen_h):
    """One integration step. Returns cursor x/y, surface impact, and cursor pull."""
    # Ball speed BEFORE we integrate — used to decide how hard the ball can drag
    # the cursor. A spinning ball builds momentum and yanks the cursor harder.
    ball = nodes[-1]
    ball_momentum_x = ball.x - ball.px
    ball_momentum_y = ball.y - ball.py
    ball_speed = math.hypot(ball_momentum_x, ball_momentum_y)

    # Cursor (node 0): pinned position to system mouse before solve;
    # mass behaviour determined by Ctrl state and ball momentum.
    cursor_node = nodes[0]
    cursor_node.x  = sys_cx
    cursor_node.y  = sys_cy
    cursor_node.px = sys_cx       # external velocity comes from mouse delta
    cursor_node.py = sys_cy
    if ctrl_held:
        cursor_node.inv_mass = 0.0
    else:
        eff_cursor_mass = CURSOR_MASS_BASE / (1.0 + ball_speed / CURSOR_SPEED_REF)
        if eff_cursor_mass < CURSOR_MIN_MASS:
            eff_cursor_mass = CURSOR_MIN_MASS
        cursor_node.inv_mass = 1.0 / eff_cursor_mass

    # Verlet integrate everyone except node 0 (cursor is externally driven)
    for n in nodes[1:]:
        vx = (n.x - n.px) * AIR_DAMP
        vy = (n.y - n.py) * AIR_DAMP
        n.px, n.py = n.x, n.y
        n.x += vx
        n.y += vy + GRAVITY

    # Distance constraints
    for _ in range(CONSTRAINT_ITERS):
        for i in range(NUM_LINKS):
            a = nodes[i]
            b = nodes[i + 1]
            dx = b.x - a.x
            dy = b.y - a.y
            d  = math.hypot(dx, dy)
            if d < 1e-6:
                continue
            wsum = a.inv_mass + b.inv_mass
            if wsum == 0.0:
                continue
            diff = (d - LINK_LEN) / d
            ax = dx * diff * (a.inv_mass / wsum)
            ay = dy * diff * (a.inv_mass / wsum)
            bx = dx * diff * (b.inv_mass / wsum)
            by = dy * diff * (b.inv_mass / wsum)
            a.x += ax
            a.y += ay
            b.x -= bx
            b.y -= by

    pull_x = 0.0
    pull_y = 0.0
    if not ctrl_held:
        ball = nodes[-1]
        solver_pull_x = cursor_node.x - sys_cx
        solver_pull_y = cursor_node.y - sys_cy

        direct_dist = math.hypot(ball.x - sys_cx, ball.y - sys_cy)
        tautness = (direct_dist - CHAIN_TOTAL_LEN * 0.62) / (CHAIN_TOTAL_LEN * 0.38)
        tautness = max(0.0, min(1.0, tautness))

        pull_x = solver_pull_x * CURSOR_PULL_GAIN + ball_momentum_x * BALL_CURSOR_DRAG * tautness
        pull_y = solver_pull_y * CURSOR_PULL_GAIN + ball_momentum_y * BALL_CURSOR_DRAG * tautness
        pull_len = math.hypot(pull_x, pull_y)
        if pull_len > CURSOR_PULL_MAX:
            scale = CURSOR_PULL_MAX / pull_len
            pull_x *= scale
            pull_y *= scale
        cursor_node.x = sys_cx + pull_x
        cursor_node.y = sys_cy + pull_y

    # Ball (last node) — collide with screen edges
    ball = nodes[-1]
    impact = 0.0
    vx = ball.x - ball.px
    vy = ball.y - ball.py
    if ball.y > screen_h - BALL_RADIUS:
        ball.y = screen_h - BALL_RADIUS
        impact = max(impact, abs(vy))
        # reflect velocity via verlet (px controls velocity)
        ball.py = ball.y + (ball.y - ball.py) * GROUND_BOUNCE
        ball.px = ball.x - (ball.x - ball.px) * GROUND_FRICTION
    if ball.x < BALL_RADIUS:
        ball.x = BALL_RADIUS
        impact = max(impact, abs(vx))
        ball.px = ball.x + (ball.x - ball.px) * WALL_BOUNCE
    if ball.x > screen_w - BALL_RADIUS:
        ball.x = screen_w - BALL_RADIUS
        impact = max(impact, abs(vx))
        ball.px = ball.x + (ball.x - ball.px) * WALL_BOUNCE
    if ball.y < BALL_RADIUS:
        ball.y = BALL_RADIUS
        impact = max(impact, abs(vy))
        ball.py = ball.y + (ball.y - ball.py) * WALL_BOUNCE

    return cursor_node.x, cursor_node.y, impact, pull_x, pull_y


# =====================================================================
# Render
# =====================================================================

def render_scene(canvas: pygame.Surface, nodes, ball_surface, link_tex,
                 ctrl_held: bool, ctrl_overlay_pos, frame_x: int, frame_y: int):
    """Render scene into `canvas` (size FRAME_W x FRAME_H) translated so
    world position (frame_x, frame_y) maps to canvas (0, 0). `ball_surface`
    is the freshly-rendered ball for this frame (orientation already baked in)."""
    canvas.fill((0, 0, 0, 0))

    # Chain: rotate the link texture along each segment and blit at midpoint
    for i in range(NUM_LINKS):
        a = nodes[i]
        b = nodes[i + 1]
        mx = (a.x + b.x) * 0.5 - frame_x
        my = (a.y + b.y) * 0.5 - frame_y
        angle_deg = -math.degrees(math.atan2(b.y - a.y, b.x - a.x))
        rotated = pygame.transform.rotozoom(link_tex, angle_deg, 1.0)
        rect = rotated.get_rect(center=(int(mx), int(my)))
        canvas.blit(rotated, rect)

    # Ball — already shaded with current rotation
    ball = nodes[-1]
    rect = ball_surface.get_rect(center=(int(ball.x - frame_x), int(ball.y - frame_y)))
    canvas.blit(ball_surface, rect)

    # Reposition crosshair when Ctrl held
    if ctrl_held:
        cx = ctrl_overlay_pos[0] - frame_x
        cy = ctrl_overlay_pos[1] - frame_y
        col = (0, 220, 255, 230)
        pygame.draw.circle(canvas, col, (int(cx), int(cy)), 22, 2)
        pygame.draw.circle(canvas, (0, 220, 255, 120), (int(cx), int(cy)), 38, 1)
        pygame.draw.line(canvas, col, (cx - 55, cy), (cx - 25, cy), 2)
        pygame.draw.line(canvas, col, (cx + 25, cy), (cx + 55, cy), 2)
        pygame.draw.line(canvas, col, (cx, cy - 55), (cx, cy - 25), 2)
        pygame.draw.line(canvas, col, (cx, cy + 25), (cx, cy + 55), 2)


def compute_frame_origin(nodes, ctrl_held: bool, screen_w: int, screen_h: int):
    """Pick the top-left corner of a FRAME_W x FRAME_H window centered on the scene."""
    cursor = nodes[0]
    ball   = nodes[-1]
    cx = (cursor.x + ball.x) * 0.5
    cy = (cursor.y + ball.y) * 0.5
    fx = int(cx - FRAME_W / 2)
    fy = int(cy - FRAME_H / 2)
    # Clamp so the frame stays attached to the scene even near screen edges
    # (negative origin is fine for a layered window — content just clips offscreen).
    return fx, fy


def surface_to_bgra_premult(surface: pygame.Surface) -> bytes:
    """Convert a pygame SRCALPHA surface to BGRA premultiplied bytes for Win32."""
    # pygame can hand us BGRA directly; premultiply alpha with numpy.
    arr = pygame.surfarray.pixels3d(surface)            # (w, h, 3) RGB, view
    alpha = pygame.surfarray.pixels_alpha(surface)      # (w, h),   view
    # arr is (w, h, 3); we want shape (h, w, 4) BGRA
    rgb = np.transpose(arr, (1, 0, 2))                  # (h, w, 3)
    a   = np.transpose(alpha, (1, 0))                   # (h, w)
    af  = a.astype(np.uint16)
    pm  = ((rgb.astype(np.uint16) * af[..., None]) // 255).astype(np.uint8)
    bgra = np.dstack([pm[..., 2], pm[..., 1], pm[..., 0], a])
    out = bgra.tobytes()
    # release surface locks
    del arr, alpha
    return out


def key_down(vk: int) -> bool:
    return bool(win32api.GetAsyncKeyState(vk) & 0x8000)


class SoundBank:
    def __init__(self, root_dir: str):
        self.enabled = False
        self.last_drop_ms = -10_000
        self.last_chain_ms = -10_000
        self.drop_sounds = []
        self.chain_sounds = []

        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
            self.drop_sounds = self._load_pitch_variants(
                os.path.join(root_dir, "drop.mp3"),
                (0.88, 0.94, 1.0, 1.07, 1.14),
            )
            self.chain_sounds = self._load_pitch_variants(
                os.path.join(root_dir, "chain.mp3"),
                (0.90, 0.96, 1.0, 1.05, 1.11),
            )
            self.enabled = bool(self.drop_sounds or self.chain_sounds)
        except Exception:
            self.enabled = False

    def _load_pitch_variants(self, path: str, pitches) -> list[pygame.mixer.Sound]:
        if not os.path.exists(path):
            return []
        base = pygame.mixer.Sound(path)
        arr = pygame.sndarray.array(base)
        sounds = []
        for pitch in pitches:
            sounds.append(self._pitch_shift(arr, pitch))
        return sounds

    @staticmethod
    def _pitch_shift(arr: np.ndarray, pitch: float) -> pygame.mixer.Sound:
        samples = arr.astype(np.float32)
        source_len = samples.shape[0]
        target_len = max(1, int(source_len / pitch))
        positions = np.linspace(0, source_len - 1, target_len, dtype=np.float32)
        left = np.floor(positions).astype(np.int32)
        right = np.minimum(left + 1, source_len - 1)
        frac = (positions - left).reshape((-1,) + (1,) * (samples.ndim - 1))
        shifted = samples[left] * (1.0 - frac) + samples[right] * frac
        shifted = np.clip(shifted, -32768, 32767).astype(arr.dtype)
        return pygame.sndarray.make_sound(np.ascontiguousarray(shifted))

    @staticmethod
    def _play(sounds, volume: float) -> None:
        if not sounds:
            return
        channel = random.choice(sounds).play()
        if channel:
            channel.set_volume(max(0.0, min(1.0, volume)))

    def update(self, chain_motion: float, impact: float) -> None:
        if not self.enabled:
            return
        now = pygame.time.get_ticks()
        if impact >= DROP_IMPACT_MIN and now - self.last_drop_ms >= DROP_SOUND_COOLDOWN:
            volume = 0.18 + min(0.72, impact / 15.0)
            self._play(self.drop_sounds, volume)
            self.last_drop_ms = now
        if chain_motion >= CHAIN_SOUND_MIN and now - self.last_chain_ms >= CHAIN_SOUND_COOLDOWN:
            volume = 0.08 + min(0.34, chain_motion / 18.0)
            self._play(self.chain_sounds, volume)
            self.last_chain_ms = now


# =====================================================================
# Main
# =====================================================================

def main() -> int:
    user32.SetProcessDPIAware()
    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)

    pygame.mixer.pre_init(44100, -16, 2, 512)
    pygame.init()
    # Drawing happens on a small off-screen surface (FRAME_W x FRAME_H).
    # The layered window is the same size and is repositioned each frame to
    # follow the chain — only ~0.6 MB pushed per frame instead of fullscreen.
    canvas = pygame.Surface((FRAME_W, FRAME_H), pygame.SRCALPHA)

    overlay = LayeredOverlay(FRAME_W, FRAME_H)

    ball_renderer = IronBallRenderer(BALL_RADIUS)
    link_tex = bake_chain_link(length=LINK_LEN + 4, ring_thickness=2.6, ring_radius=4.0)
    audio = SoundBank(os.path.dirname(os.path.abspath(__file__)))

    sys_cx, sys_cy = win32api.GetCursorPos()
    nodes = build_chain(sys_cx, sys_cy)
    ball_yaw = 0.0
    ball_pitch = 0.0

    clock = pygame.time.Clock()
    running = True

    while running:
        if not overlay.pump():
            break
        if all(key_down(vk) for vk in QUIT_VKS):
            break

        sys_cx, sys_cy = win32api.GetCursorPos()
        ctrl_held = key_down(VK_LCONTROL) or key_down(VK_RCONTROL)

        post_cx, post_cy = sys_cx, sys_cy
        max_impact = 0.0
        for _ in range(SUBSTEPS):
            post_cx, post_cy, impact, pull_x, pull_y = physics_step(
                nodes, sys_cx, sys_cy, ctrl_held, screen_w, screen_h,
            )
            max_impact = max(max_impact, impact)
            sys_cx, sys_cy = post_cx, post_cy

        # If the solver moved the cursor node away from the OS cursor, sync the OS.
        os_cx, os_cy = win32api.GetCursorPos()
        new_cx = max(0, min(screen_w - 1, int(round(post_cx))))
        new_cy = max(0, min(screen_h - 1, int(round(post_cy))))
        if (new_cx, new_cy) != (os_cx, os_cy):
            win32api.SetCursorPos((new_cx, new_cy))

        frame_x, frame_y = compute_frame_origin(nodes, ctrl_held, screen_w, screen_h)
        ball = nodes[-1]
        ball_vx = ball.x - ball.px
        ball_vy = ball.y - ball.py
        ball_yaw += (ball_vx / BALL_RADIUS) * BALL_SPIN_RESPONSE
        ball_pitch -= (ball_vy / BALL_RADIUS) * BALL_SPIN_RESPONSE
        ball_surface = ball_renderer.render(ball_yaw, ball_pitch)
        chain_motion = sum(
            math.hypot(n.x - n.px, n.y - n.py) for n in nodes[1:]
        ) / NUM_LINKS
        audio.update(chain_motion, max_impact)
        render_scene(canvas, nodes, ball_surface, link_tex, ctrl_held,
                     (new_cx, new_cy), frame_x, frame_y)
        overlay.push(surface_to_bgra_premult(canvas), frame_x, frame_y)

        clock.tick(FPS)

    overlay.close()
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
