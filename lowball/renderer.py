import math
import numpy as np
import pygame
from lowball import config

# World lighting vector (normalized)
LIGHT_DIR = np.array([-0.45, -0.55, 0.70])
LIGHT_DIR /= np.linalg.norm(LIGHT_DIR)


class IronBallRenderer:
    """Per-frame sphere renderer with fixed world lighting and rotating metal texture."""

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
    w = int(math.ceil(length)) + pad * 2
    h = int(math.ceil((ring_radius + ring_thickness) * 2)) + pad * 2

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx = (w - 1) / 2.0
    cy = (h - 1) / 2.0
    dx = xx - cx
    dy = yy - cy

    dx_c = np.clip(dx, -inner_len, inner_len)
    line_dist = np.sqrt((dx - dx_c) ** 2 + dy ** 2)

    edge = line_dist - ring_radius
    abs_edge = np.abs(edge)

    rho = np.clip(edge / ring_thickness, -1.0, 1.0)
    nz_local = np.sqrt(np.maximum(0.0, 1.0 - rho * rho))

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

    alpha = np.clip(ring_thickness - abs_edge + 0.5, 0.0, 1.0) * 255.0

    rgba = np.dstack([rgb, alpha]).astype(np.uint8)
    surf = pygame.image.frombuffer(rgba.tobytes(), (w, h), 'RGBA').copy()
    return surf


def render_scene(canvas: pygame.Surface, nodes, ball_surface, link_tex,
                 ctrl_held: bool, ctrl_overlay_pos, frame_x: int, frame_y: int):
    """Render scene into `canvas` (translated so world position maps to canvas)."""
    canvas.fill((0, 0, 0, 0))

    for i in range(config.NUM_LINKS):
        a = nodes[i]
        b = nodes[i + 1]
        mx = (a.x + b.x) * 0.5 - frame_x
        my = (a.y + b.y) * 0.5 - frame_y
        angle_deg = -math.degrees(math.atan2(b.y - a.y, b.x - a.x))
        rotated = pygame.transform.rotozoom(link_tex, angle_deg, 1.0)
        rect = rotated.get_rect(center=(int(mx), int(my)))
        canvas.blit(rotated, rect)

    ball = nodes[-1]
    rect = ball_surface.get_rect(center=(int(ball.x - frame_x), int(ball.y - frame_y)))
    canvas.blit(ball_surface, rect)

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
    cursor = nodes[0]
    ball   = nodes[-1]
    cx = (cursor.x + ball.x) * 0.5
    cy = (cursor.y + ball.y) * 0.5
    fx = int(cx - config.FRAME_W / 2)
    fy = int(cy - config.FRAME_H / 2)
    return fx, fy


def surface_to_bgra_premult(surface: pygame.Surface) -> bytes:
    arr = pygame.surfarray.pixels3d(surface)            
    alpha = pygame.surfarray.pixels_alpha(surface)      
    
    rgb = np.transpose(arr, (1, 0, 2))                  
    a   = np.transpose(alpha, (1, 0))                   
    af  = a.astype(np.uint16)
    pm  = ((rgb.astype(np.uint16) * af[..., None]) // 255).astype(np.uint8)
    bgra = np.dstack([pm[..., 2], pm[..., 1], pm[..., 0], a])
    out = bgra.tobytes()
    del arr, alpha
    return out
