import os
import sys
import math
import pygame
import win32api
import win32con

from lowball import config
from lowball.window import LayeredOverlay, key_down, user32
from lowball.physics import build_chain, physics_step
from lowball.renderer import IronBallRenderer, bake_chain_link, render_scene, compute_frame_origin, surface_to_bgra_premult
from lowball.audio import SoundBank

def main() -> int:
    # Set Process DPI Awareness
    user32.SetProcessDPIAware()
    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)

    # Initialize Pygame Mixer and Core
    pygame.mixer.pre_init(44100, -16, 2, 512)
    pygame.init()
    canvas = pygame.Surface((config.FRAME_W, config.FRAME_H), pygame.SRCALPHA)

    # Initialize layered transparent window
    overlay = LayeredOverlay(config.FRAME_W, config.FRAME_H)

    # Initialize renderers and audio
    ball_renderer = IronBallRenderer(config.BALL_RADIUS)
    link_tex = bake_chain_link(
        length=config.LINK_LEN + 4,
        ring_thickness=2.6,
        ring_radius=4.0
    )
    
    # Resolve assets relative to this package file
    package_dir = os.path.dirname(os.path.abspath(__file__))
    assets_dir = os.path.join(package_dir, "assets")
    audio = SoundBank(assets_dir)

    # Setup physics simulation nodes starting at current cursor position
    sys_cx, sys_cy = win32api.GetCursorPos()
    nodes = build_chain(sys_cx, sys_cy)
    ball_yaw = 0.0
    ball_pitch = 0.0

    clock = pygame.time.Clock()
    running = True

    while running:
        # Pump overlay message queue. If WM_QUIT is received, exit.
        if not overlay.pump():
            break
            
        # Exit if Escape is pressed, or if Ctrl+Shift+Q is pressed
        if key_down(config.EXIT_VK):
            break
        if all(key_down(vk) for vk in config.QUIT_VKS):
            break

        sys_cx, sys_cy = win32api.GetCursorPos()
        ctrl_held = key_down(config.VK_LCONTROL) or key_down(config.VK_RCONTROL)

        post_cx, post_cy = sys_cx, sys_cy
        max_impact = 0.0
        for _ in range(config.SUBSTEPS):
            post_cx, post_cy, impact, pull_x, pull_y = physics_step(
                nodes, sys_cx, sys_cy, ctrl_held, screen_w, screen_h,
            )
            max_impact = max(max_impact, impact)
            sys_cx, sys_cy = post_cx, post_cy

        # Update cursor position on screen to match physics state if necessary
        os_cx, os_cy = win32api.GetCursorPos()
        new_cx = max(0, min(screen_w - 1, int(round(post_cx))))
        new_cy = max(0, min(screen_h - 1, int(round(post_cy))))
        if (new_cx, new_cy) != (os_cx, os_cy):
            win32api.SetCursorPos((new_cx, new_cy))

        # Position viewport window and render the scene
        frame_x, frame_y = compute_frame_origin(nodes, ctrl_held, screen_w, screen_h)
        ball = nodes[-1]
        ball_vx = ball.x - ball.px
        ball_vy = ball.y - ball.py
        ball_yaw += (ball_vx / config.BALL_RADIUS) * config.BALL_SPIN_RESPONSE
        ball_pitch -= (ball_vy / config.BALL_RADIUS) * config.BALL_SPIN_RESPONSE
        
        ball_surface = ball_renderer.render(ball_yaw, ball_pitch)
        chain_motion = sum(
            math.hypot(n.x - n.px, n.y - n.py) for n in nodes[1:]
        ) / config.NUM_LINKS
        
        audio.update(chain_motion, max_impact)
        render_scene(
            canvas, nodes, ball_surface, link_tex, ctrl_held,
            (new_cx, new_cy), frame_x, frame_y
        )
        overlay.push(surface_to_bgra_premult(canvas), frame_x, frame_y)

        clock.tick(config.FPS)

    overlay.close()
    pygame.quit()
    return 0

if __name__ == "__main__":
    sys.exit(main())
