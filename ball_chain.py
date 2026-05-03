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
GRAVITY             = 2.36
AIR_DAMP_PER_SEC    = 0.82          # fraction of velocity that survives 1 second of air
GROUND_BOUNCE       = 0.1
GROUND_FRICTION     = 0.94
WALL_BOUNCE         = 0.55
CONSTRAINT_ITERS    = 14
SUBSTEPS            = 2
FPS                 = 60
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
HWND_TOPMOST        = -1
SWP_NOMOVE          = 0x0002
SWP_NOSIZE          = 0x0001
SWP_NOACTIVATE      = 0x0010


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

    def __init__(self, width: int, height: int):
        self.w = width
        self.h = height

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
        cls_name = f"BallChainOverlayCls_{id(self)}"
        wc.lpszClassName = cls_name
        wc.lpfnWndProc   = self._wndproc
        wc.hInstance     = win32api.GetModuleHandle(None)
        wc.hbrBackground = 0
        wc.style         = 0
        self.class_atom  = win32gui.RegisterClass(wc)

        self.hwnd = win32gui.CreateWindowEx(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST
            | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            self.class_atom,
            "BallChainOverlay",
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
        self._push_count = 0

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
        # Re-assert topmost Z-order once per second to stay above the taskbar
        # without causing z-order thrashing / flicker between overlay windows.
        self._push_count += 1
        if self._push_count % 100 == 0:
            win32gui.SetWindowPos(
                self.hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
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


BALL_CACHE_STEP = 0.06  # ~3.4 degrees; imperceptible quantization


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

        self._cache: dict[tuple[float, float], pygame.Surface] = {}
        self._cache_max = 256

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
        step = BALL_CACHE_STEP
        q_yaw = round(yaw / step) * step
        q_pitch = round(pitch / step) * step
        key = (q_yaw, q_pitch)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        sy, cy = math.sin(-q_yaw), math.cos(-q_yaw)
        sp, cp = math.sin(-q_pitch), math.cos(-q_pitch)

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
        surf = pygame.image.frombuffer(rgba.tobytes(), (w, h), 'RGBA').copy()

        if len(self._cache) >= self._cache_max:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = surf
        return surf


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

LINK_ANGLE_STEP = 2.0  # degrees — cache rotated link textures at 2° resolution
_link_rot_cache: dict[float, pygame.Surface] = {}


def _get_rotated_link(link_tex: pygame.Surface, angle_deg: float) -> pygame.Surface:
    q = round(angle_deg / LINK_ANGLE_STEP) * LINK_ANGLE_STEP
    cached = _link_rot_cache.get(q)
    if cached is not None:
        return cached
    rotated = pygame.transform.rotozoom(link_tex, q, 1.0)
    _link_rot_cache[q] = rotated
    return rotated


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
        rotated = _get_rotated_link(link_tex, angle_deg)
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


# Pre-allocated work buffers for surface_to_bgra_premult, keyed by (w, h).
# Avoids repeated numpy allocation / GC churn on every frame.
_premult_bufs: dict = {}


def surface_to_bgra_premult(surface: pygame.Surface) -> bytes:
    """Convert a pygame SRCALPHA surface to BGRA premultiplied bytes for Win32.
    Uses per-size pre-allocated buffers to avoid per-frame heap allocation."""
    w, h = surface.get_size()
    key = (w, h)
    if key not in _premult_bufs:
        _premult_bufs[key] = {
            'af':   np.empty((h, w),    dtype=np.uint16),
            'rgb16': np.empty((h, w, 3), dtype=np.uint16),
            'bgra':  np.empty((h, w, 4), dtype=np.uint8),
        }
    bufs = _premult_bufs[key]

    arr   = pygame.surfarray.pixels3d(surface)   # (w, h, 3) view, no copy
    alpha = pygame.surfarray.pixels_alpha(surface)  # (w, h) view, no copy

    # Transpose into (h, w) layout in-place via pre-allocated buffers
    af   = bufs['af']
    rgb16 = bufs['rgb16']
    bgra  = bufs['bgra']

    np.copyto(af,    alpha.T.astype(np.uint16))
    np.copyto(rgb16, np.transpose(arr, (1, 0, 2)).astype(np.uint16))
    del arr, alpha   # release surface locks immediately

    # Premultiply each channel: pm = (rgb * a) // 255
    np.multiply(rgb16[:, :, 2], af, out=rgb16[:, :, 2])  # B (src R -> dst B)
    np.floor_divide(rgb16[:, :, 2], 255, out=rgb16[:, :, 2])
    np.multiply(rgb16[:, :, 1], af, out=rgb16[:, :, 1])  # G
    np.floor_divide(rgb16[:, :, 1], 255, out=rgb16[:, :, 1])
    np.multiply(rgb16[:, :, 0], af, out=rgb16[:, :, 0])  # R (src B -> dst R)
    np.floor_divide(rgb16[:, :, 0], 255, out=rgb16[:, :, 0])

    # Pack into BGRA: dst[B,G,R,A] = [src_R_pm, src_G_pm, src_B_pm, alpha]
    np.copyto(bgra[:, :, 0], rgb16[:, :, 2].astype(np.uint8))  # B
    np.copyto(bgra[:, :, 1], rgb16[:, :, 1].astype(np.uint8))  # G
    np.copyto(bgra[:, :, 2], rgb16[:, :, 0].astype(np.uint8))  # R
    np.copyto(bgra[:, :, 3], af.astype(np.uint8))               # A

    return bgra.tobytes()


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
# Stop button (clickable, randomly repositioning)
# =====================================================================

STOP_BTN_W  = 110
STOP_BTN_H  = 38
STOP_REPOSITION_MS = 1000  # relocate every 1 second


class StopButton:
    """A small clickable topmost window that says STOP.
    Repositions randomly in the upper half of the screen every second."""

    def __init__(self, screen_w: int, screen_h: int):
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.clicked = False
        self._last_move_ms = 0

        WNDPROC = ctypes.WINFUNCTYPE(
            LRESULT, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM,
        )

        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == win32con.WM_LBUTTONDOWN:
                self.clicked = True
                return 0
            if msg == win32con.WM_DESTROY:
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = WNDPROC(wnd_proc)

        wc = win32gui.WNDCLASS()
        cls_name = f"StopBtnCls_{id(self)}"
        wc.lpszClassName = cls_name
        wc.lpfnWndProc   = self._wndproc
        wc.hInstance     = win32api.GetModuleHandle(None)
        wc.hbrBackground = 0
        wc.style         = 0
        self._class_atom = win32gui.RegisterClass(wc)

        # NOT WS_EX_TRANSPARENT — this window must receive clicks.
        self.hwnd = win32gui.CreateWindowEx(
            WS_EX_LAYERED | WS_EX_TOPMOST
            | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            self._class_atom,
            "StopBtn",
            WS_POPUP,
            0, 0, STOP_BTN_W, STOP_BTN_H,
            0, 0, wc.hInstance, None,
        )
        win32gui.ShowWindow(self.hwnd, SW_SHOW)

        # DIB section for per-pixel alpha
        self.hdc_screen = user32.GetDC(0)
        self.hdc_mem    = gdi32.CreateCompatibleDC(self.hdc_screen)

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize        = sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth       = STOP_BTN_W
        bmi.bmiHeader.biHeight      = -STOP_BTN_H
        bmi.bmiHeader.biPlanes      = 1
        bmi.bmiHeader.biBitCount    = 32
        bmi.bmiHeader.biCompression = BI_RGB

        self.p_bits = c_void_p()
        self.hbmp = gdi32.CreateDIBSection(
            self.hdc_mem, byref(bmi), 0, byref(self.p_bits), 0, 0,
        )
        self.old_obj = gdi32.SelectObject(self.hdc_mem, self.hbmp)
        self._byte_count = STOP_BTN_W * STOP_BTN_H * 4

        # Pre-render the button surface once
        self._btn_bytes = self._render_button()
        self._push_count = 0

        # Initial random position & push
        self.x, self.y = self._random_pos()
        self._push()

    def _random_pos(self) -> tuple[int, int]:
        x = random.randint(20, max(20, self.screen_w - STOP_BTN_W - 20))
        y = random.randint(20, max(20, self.screen_h // 2 - STOP_BTN_H - 20))
        return x, y

    @staticmethod
    def _render_button() -> bytes:
        """Render a small styled STOP button to BGRA premultiplied bytes."""
        surf = pygame.Surface((STOP_BTN_W, STOP_BTN_H), pygame.SRCALPHA)
        # Rounded rect background
        pygame.draw.rect(surf, (200, 40, 40, 220),
                         (0, 0, STOP_BTN_W, STOP_BTN_H), border_radius=8)
        pygame.draw.rect(surf, (255, 80, 80, 180),
                         (0, 0, STOP_BTN_W, STOP_BTN_H), width=2, border_radius=8)
        # Text
        font = pygame.font.SysFont("Segoe UI", 18, bold=True)
        txt  = font.render("STOP", True, (255, 255, 255))
        surf.blit(txt, ((STOP_BTN_W - txt.get_width()) // 2,
                        (STOP_BTN_H - txt.get_height()) // 2))
        return surface_to_bgra_premult(surf)

    def _push(self) -> None:
        ctypes.memmove(self.p_bits, self._btn_bytes, self._byte_count)
        pt_src = POINT(0, 0)
        pt_dst = POINT(self.x, self.y)
        sz     = SIZE(STOP_BTN_W, STOP_BTN_H)
        blend  = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        user32.UpdateLayeredWindow(
            self.hwnd, self.hdc_screen, byref(pt_dst), byref(sz),
            self.hdc_mem, byref(pt_src), 0, byref(blend), ULW_ALPHA,
        )
        self._push_count += 1
        if self._push_count % 100 == 0:
            win32gui.SetWindowPos(
                self.hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
            )

    def update(self) -> bool:
        """Call each frame. Returns True if STOP was clicked."""
        # Pump messages for this window
        msg = wintypes.MSG()
        while user32.PeekMessageW(byref(msg), self.hwnd, 0, 0, 1):
            user32.TranslateMessage(byref(msg))
            user32.DispatchMessageW(byref(msg))

        # Reposition every second
        now = pygame.time.get_ticks()
        if now - self._last_move_ms >= STOP_REPOSITION_MS:
            self._last_move_ms = now
            self.x, self.y = self._random_pos()
            self._push()

        return self.clicked

    def close(self):
        gdi32.SelectObject(self.hdc_mem, self.old_obj)
        gdi32.DeleteObject(self.hbmp)
        gdi32.DeleteDC(self.hdc_mem)
        user32.ReleaseDC(0, self.hdc_screen)
        win32gui.DestroyWindow(self.hwnd)


# =====================================================================
# Phase system
# =====================================================================

# Each phase: (name, description, duration_seconds)
PHASE_POOL = [
    (
        "Chained Up",
        "Your cursor remains chained up to a ball. Every move drags it along.",
        30.0,
    ),
]

PHASE_TRANSITION_LEAD = 2.0    # seconds before end to play phase.mp3 & cue swap
# HUD: a full-width strip at the screen bottom (gradient spans entire width)
HUD_HEIGHT              = 480  # height of the HUD strip in pixels
HUD_FONT_TITLE_SIZE     = 32
HUD_FONT_DESC_SIZE      = 15
HUD_BAR_H               = 4   # thin, sleek progress bar
HUD_BAR_MAX_W           = 400  # max bar width, centered
HUD_BAR_SIDE_PAD        = 60   # fallback padding if screen narrower than bar max


class PhaseManager:
    """Manages the current phase, timing, transitions, and related audio."""

    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        self.phase_sound: pygame.mixer.Sound | None = None
        self._load_phase_sound()

        self._phase_triggered = False   # True once phase.mp3 has fired this cycle
        self._pick_phase()              # sets self.current, self.start_ms, self.duration
        self._start_music()

    # ------------------------------------------------------------------
    def _load_phase_sound(self) -> None:
        path = os.path.join(self.root_dir, "phase.mp3")
        try:
            if os.path.exists(path):
                self.phase_sound = pygame.mixer.Sound(path)
        except Exception:
            self.phase_sound = None

    def _start_music(self) -> None:
        music_path = os.path.join(self.root_dir, "music.mp3")
        try:
            if os.path.exists(music_path):
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
                pygame.mixer.music.load(music_path)
                pygame.mixer.music.set_volume(0.55)
                pygame.mixer.music.play(-1)  # loop indefinitely
        except Exception:
            pass

    def _pick_phase(self) -> None:
        self.current = random.choice(PHASE_POOL)
        self.start_ms = pygame.time.get_ticks()
        self.duration = self.current[2]  # seconds
        self._phase_triggered = False

    # ------------------------------------------------------------------
    def update(self) -> None:
        """Call once per frame. Handles phase.mp3 trigger and phase rotation."""
        now_ms = pygame.time.get_ticks()
        elapsed = (now_ms - self.start_ms) / 1000.0
        remaining = self.duration - elapsed

        # 2 seconds before end: play phase.mp3 once
        if not self._phase_triggered and remaining <= PHASE_TRANSITION_LEAD:
            self._phase_triggered = True
            if self.phase_sound:
                self.phase_sound.play()

        # Phase is over → next phase
        if remaining <= 0.0:
            self._pick_phase()
            self._start_music()

    # ------------------------------------------------------------------
    def state(self) -> tuple[str, str, float, float]:
        """Returns (name, description, elapsed_s, total_s)."""
        now_ms = pygame.time.get_ticks()
        elapsed = (now_ms - self.start_ms) / 1000.0
        return self.current[0], self.current[1], elapsed, self.duration


# =====================================================================
# HUD rendering
# =====================================================================

_hud_fonts: dict = {}


def _get_font(size: int, bold: bool = True) -> pygame.font.Font:
    key = (size, bold)
    if key not in _hud_fonts:
        _hud_fonts[key] = pygame.font.SysFont("Segoe UI", size, bold=bold)
    return _hud_fonts[key]


def _bake_hud_gradient(strip_w: int, strip_h: int) -> pygame.Surface:
    """Pre-build the full-width gradient background as a pygame Surface.
    Transparent at top, semi-opaque black at bottom. Called once at startup."""
    rows = np.arange(strip_h, dtype=np.float32)
    t = rows / strip_h                      # 0=top, 1=bottom
    alpha = (t * t * 245).astype(np.uint8)  # quadratic fade, max ~245
    arr = np.zeros((strip_h, strip_w, 4), dtype=np.uint8)
    arr[:, :, 3] = alpha[:, np.newaxis]     # RGBA: R=G=B=0, only alpha varies
    surf = pygame.image.frombuffer(arr.tobytes(), (strip_w, strip_h), 'RGBA')
    return surf.copy()


def _draw_glow_text(canvas, font, text, cx, cy, color, glow_color, glow_radius=2):
    """Draw text with a soft glow halo behind it for a polished look."""
    glow_surf = font.render(text, True, glow_color)
    glow_surf.set_alpha(50)
    gw, gh = glow_surf.get_size()
    for ox, oy in [(-glow_radius, 0), (glow_radius, 0),
                    (0, -glow_radius), (0, glow_radius),
                    (-glow_radius, -glow_radius), (glow_radius, -glow_radius),
                    (-glow_radius, glow_radius), (glow_radius, glow_radius)]:
        canvas.blit(glow_surf, (cx - gw // 2 + ox, cy - gh // 2 + oy))
    main_surf = font.render(text, True, color)
    main_surf.set_alpha(240)
    canvas.blit(main_surf, (cx - main_surf.get_width() // 2,
                            cy - main_surf.get_height() // 2))
    return main_surf.get_width(), main_surf.get_height()


def render_hud(
    canvas: pygame.Surface,
    grad_surf: pygame.Surface,
    phase_name: str,
    phase_desc: str,
    elapsed: float,
    total: float,
) -> None:
    """Draw a polished phase HUD onto `canvas` (screen_w × HUD_HEIGHT).
    Layout top-to-bottom: title → separator → description → gap → bar → timer."""
    canvas.fill((0, 0, 0, 0))

    remaining = max(0.0, total - elapsed)
    near_end  = remaining <= PHASE_TRANSITION_LEAD
    now_s     = pygame.time.get_ticks() / 1000.0

    strip_w = canvas.get_width()
    strip_h = canvas.get_height()
    center_x = strip_w // 2

    # --- Gradient (pre-baked, free blit) ---
    canvas.blit(grad_surf, (0, 0))

    # --- Earthquake shake (only in last 2 seconds) ---
    if near_end:
        shake_amp  = 6.0
        shake_freq = 28.0
        sx = int(shake_amp * math.sin(now_s * shake_freq * 1.3))
        sy = int(shake_amp * math.cos(now_s * shake_freq))
    else:
        sx = sy = 0

    # ---- LAYOUT (bottom-up positioning, shifted up) ----
    bar_w    = min(HUD_BAR_MAX_W, strip_w - HUD_BAR_SIDE_PAD * 2)
    bar_x    = (strip_w - bar_w) // 2
    bar_y    = strip_h - 80
    progress = max(0.0, min(1.0, remaining / total)) if total > 0 else 0.0
    fill_w   = int(bar_w * progress)

    time_y   = bar_y + HUD_BAR_H + 6     # timer below bar
    desc_y   = bar_y - 28                 # description above bar
    title_y  = desc_y - 36                # title above description

    # --- Title (uppercase, plain white) ---
    font_title = _get_font(HUD_FONT_TITLE_SIZE)
    title_surf = font_title.render(phase_name.upper(), True, (255, 255, 255))
    title_surf.set_alpha(240)
    canvas.blit(title_surf,
                (center_x - title_surf.get_width() // 2 + sx, title_y + sy))

    # --- Description (white, smaller, below title) ---
    font_desc = _get_font(HUD_FONT_DESC_SIZE, bold=False)
    desc_surf = font_desc.render(phase_desc, True, (255, 255, 255))
    desc_surf.set_alpha(160)
    canvas.blit(desc_surf,
                (center_x - desc_surf.get_width() // 2 + sx, desc_y + sy))

    # --- Progress bar (thin, centered, rounded) ---
    pygame.draw.rect(canvas, (255, 255, 255, 30),
                     (bar_x, bar_y, bar_w, HUD_BAR_H), border_radius=2)
    if fill_w > 0:
        if near_end:
            pulse = 0.55 + 0.45 * math.sin(now_s * 10.0)
            r = int(255 * pulse)
            bar_color = (r, 50, 50, 220)
            glow_rect = pygame.Surface((fill_w + 8, HUD_BAR_H + 8), pygame.SRCALPHA)
            glow_rect.fill((255, 40, 40, int(60 * pulse)))
            canvas.blit(glow_rect, (bar_x - 4, bar_y - 4))
        else:
            bar_color = (220, 225, 240, 180)
        pygame.draw.rect(canvas, bar_color,
                         (bar_x, bar_y, fill_w, HUD_BAR_H), border_radius=2)
        if fill_w > 2:
            pip_x = bar_x + fill_w - 1
            pygame.draw.rect(canvas, (255, 255, 255, 200),
                             (pip_x - 1, bar_y, 2, HUD_BAR_H), border_radius=1)

    # --- Timer (centered below bar, clean m:ss) ---
    mins = int(remaining) // 60
    secs = int(remaining) % 60
    time_str = f"{mins}:{secs:02d}"
    font_time = _get_font(13, bold=False)
    time_surf = font_time.render(time_str, True, (200, 200, 210))
    time_surf.set_alpha(120)
    canvas.blit(time_surf,
                (center_x - time_surf.get_width() // 2, time_y))


# =====================================================================
# Main
# =====================================================================

def main() -> int:
    user32.SetProcessDPIAware()
    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)

    pygame.mixer.pre_init(44100, -16, 2, 512)
    pygame.init()

    root_dir = os.path.dirname(os.path.abspath(__file__))

    # Small chain canvas (repositioned each frame for performance)
    canvas = pygame.Surface((FRAME_W, FRAME_H), pygame.SRCALPHA)
    overlay = LayeredOverlay(FRAME_W, FRAME_H)

    # HUD: thin strip at the screen bottom — much cheaper than full-screen.
    # Pre-bake the gradient once; only dynamic elements drawn per frame.
    hud_canvas  = pygame.Surface((screen_w, HUD_HEIGHT), pygame.SRCALPHA)
    hud_grad    = _bake_hud_gradient(screen_w, HUD_HEIGHT)
    hud_overlay = LayeredOverlay(screen_w, HUD_HEIGHT)
    hud_dst_y   = screen_h - HUD_HEIGHT  # fixed Y for UpdateLayeredWindow

    ball_renderer = IronBallRenderer(BALL_RADIUS)
    link_tex = bake_chain_link(length=LINK_LEN + 4, ring_thickness=2.6, ring_radius=4.0)
    audio = SoundBank(root_dir)
    phase_mgr = PhaseManager(root_dir)
    stop_btn  = StopButton(screen_w, screen_h)

    sys_cx, sys_cy = win32api.GetCursorPos()
    nodes = build_chain(sys_cx, sys_cy)
    ball_yaw = 0.0
    ball_pitch = 0.0

    clock = pygame.time.Clock()
    frame_counter = 0
    running = True

    while running:
        if not overlay.pump():
            break
        if all(key_down(vk) for vk in QUIT_VKS):
            break
        if stop_btn.update():
            break

        # --- Phase update (handles music restart & phase.mp3 trigger) ---
        phase_mgr.update()

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

        # --- Chain render ---
        render_scene(canvas, nodes, ball_surface, link_tex, ctrl_held,
                     (new_cx, new_cy), frame_x, frame_y)
        overlay.push(surface_to_bgra_premult(canvas), frame_x, frame_y)

        # --- HUD render (throttled to ~30 fps — no need for 100 fps) ---
        if frame_counter % 3 == 0:
            p_name, p_desc, p_elapsed, p_total = phase_mgr.state()
            render_hud(hud_canvas, hud_grad, p_name, p_desc, p_elapsed, p_total)
            hud_overlay.push(surface_to_bgra_premult(hud_canvas), 0, hud_dst_y)

        frame_counter += 1
        clock.tick(FPS)

    overlay.close()
    hud_overlay.close()
    stop_btn.close()
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
