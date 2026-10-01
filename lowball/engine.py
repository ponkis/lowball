import math
import os
import sys

import pygame
import win32api

from lowball import config
from lowball.audio import SoundBank
from lowball.entities import build_chain, physics_step
from lowball.renderer import (
    IronBallRenderer,
    bake_chain_link,
    compute_frame_origin,
    render_scene,
    surface_to_bgra_premult,
)
from lowball.resources import get_asset_path
from lowball.window import LayeredOverlay, key_down, user32


class Engine:
    """Coordinate Lowball's Windows overlay, simulation, rendering, and audio."""

    def __init__(self):
        self.overlay = None

    def run(self) -> int:
        user32.SetProcessDPIAware()
        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)

        pygame.mixer.pre_init(44100, -16, 2, 512)
        pygame.init()
        canvas = pygame.Surface((config.FRAME_W, config.FRAME_H), pygame.SRCALPHA)

        self.overlay = LayeredOverlay(config.FRAME_W, config.FRAME_H)
        ball_renderer = IronBallRenderer(config.BALL_RADIUS)
        link_tex = bake_chain_link(
            length=config.LINK_LEN + 4,
            ring_thickness=2.6,
            ring_radius=4.0,
        )
        assets_dir = os.path.dirname(get_asset_path("drop.mp3"))
        audio = SoundBank(assets_dir)

        sys_cx, sys_cy = win32api.GetCursorPos()
        nodes = build_chain(sys_cx, sys_cy)
        ball_yaw = 0.0
        ball_pitch = 0.0

        clock = pygame.time.Clock()
        running = True
        try:
            while running:
                if not self.overlay.pump():
                    break

                if key_down(config.EXIT_VK):
                    break
                if all(key_down(vk) for vk in config.QUIT_VKS):
                    break

                sys_cx, sys_cy = win32api.GetCursorPos()
                ctrl_held = key_down(config.VK_LCONTROL) or key_down(
                    config.VK_RCONTROL
                )

                post_cx, post_cy = sys_cx, sys_cy
                max_impact = 0.0
                for _ in range(config.SUBSTEPS):
                    post_cx, post_cy, impact, pull_x, pull_y = physics_step(
                        nodes, sys_cx, sys_cy, ctrl_held, screen_w, screen_h
                    )
                    max_impact = max(max_impact, impact)
                    sys_cx, sys_cy = post_cx, post_cy

                os_cx, os_cy = win32api.GetCursorPos()
                new_cx = max(0, min(screen_w - 1, int(round(post_cx))))
                new_cy = max(0, min(screen_h - 1, int(round(post_cy))))
                if (new_cx, new_cy) != (os_cx, os_cy):
                    win32api.SetCursorPos((new_cx, new_cy))

                frame_x, frame_y = compute_frame_origin(
                    nodes, ctrl_held, screen_w, screen_h
                )
                ball = nodes[-1]
                ball_vx = ball.x - ball.px
                ball_vy = ball.y - ball.py
                ball_yaw += (ball_vx / config.BALL_RADIUS) * config.BALL_SPIN_RESPONSE
                ball_pitch -= (ball_vy / config.BALL_RADIUS) * config.BALL_SPIN_RESPONSE

                ball_surface = ball_renderer.render(ball_yaw, ball_pitch)
                chain_motion = sum(
                    math.hypot(node.x - node.px, node.y - node.py)
                    for node in nodes[1:]
                ) / config.NUM_LINKS

                audio.update(chain_motion, max_impact)
                render_scene(
                    canvas,
                    nodes,
                    ball_surface,
                    link_tex,
                    ctrl_held,
                    (new_cx, new_cy),
                    frame_x,
                    frame_y,
                )
                self.overlay.push(
                    surface_to_bgra_premult(canvas), frame_x, frame_y
                )

                clock.tick(config.FPS)
        finally:
            self.close()

        return 0

    def close(self) -> None:
        try:
            if self.overlay is not None:
                overlay, self.overlay = self.overlay, None
                overlay.close()
        finally:
            pygame.quit()


def main() -> int:
    engine = Engine()
    try:
        return engine.run()
    finally:
        engine.close()


if __name__ == "__main__":
    sys.exit(main())
