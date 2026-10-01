import math

from lowball import config


class Node:
    __slots__ = ("x", "y", "px", "py", "inv_mass")

    def __init__(self, x, y, mass):
        self.x = self.px = float(x)
        self.y = self.py = float(y)
        self.inv_mass = 0.0 if mass == float("inf") else 1.0 / mass


def build_chain(cx: int, cy: int):
    """Cursor (node 0) → chain links → ball (node N). Initially hanging straight down."""
    nodes = []
    for i in range(config.NUM_LINKS + 1):
        t = i / config.NUM_LINKS
        x = cx
        y = cy + config.CHAIN_TOTAL_LEN * t
        if i == 0:
            mass = float("inf")
        elif i == config.NUM_LINKS:
            mass = config.BALL_MASS
        else:
            mass = config.LINK_MASS
        nodes.append(Node(x, y, mass))
    return nodes


def physics_step(nodes, sys_cx, sys_cy, ctrl_held, screen_w, screen_h):
    """One integration step. Returns cursor x/y, surface impact, and cursor pull."""
    ball = nodes[-1]
    ball_momentum_x = ball.x - ball.px
    ball_momentum_y = ball.y - ball.py
    ball_speed = math.hypot(ball_momentum_x, ball_momentum_y)

    cursor_node = nodes[0]
    cursor_node.x = sys_cx
    cursor_node.y = sys_cy
    cursor_node.px = sys_cx
    cursor_node.py = sys_cy
    if ctrl_held:
        cursor_node.inv_mass = 0.0
    else:
        eff_cursor_mass = config.CURSOR_MASS_BASE / (
            1.0 + ball_speed / config.CURSOR_SPEED_REF
        )
        if eff_cursor_mass < config.CURSOR_MIN_MASS:
            eff_cursor_mass = config.CURSOR_MIN_MASS
        cursor_node.inv_mass = 1.0 / eff_cursor_mass

    for node in nodes[1:]:
        vx = (node.x - node.px) * config.AIR_DAMP
        vy = (node.y - node.py) * config.AIR_DAMP
        node.px, node.py = node.x, node.y
        node.x += vx
        node.y += vy + config.GRAVITY

    for _ in range(config.CONSTRAINT_ITERS):
        for i in range(config.NUM_LINKS):
            a = nodes[i]
            b = nodes[i + 1]
            dx = b.x - a.x
            dy = b.y - a.y
            distance = math.hypot(dx, dy)
            if distance < 1e-6:
                continue
            weight_sum = a.inv_mass + b.inv_mass
            if weight_sum == 0.0:
                continue
            diff = (distance - config.LINK_LEN) / distance
            ax = dx * diff * (a.inv_mass / weight_sum)
            ay = dy * diff * (a.inv_mass / weight_sum)
            bx = dx * diff * (b.inv_mass / weight_sum)
            by = dy * diff * (b.inv_mass / weight_sum)
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
        tautness = (direct_dist - config.CHAIN_TOTAL_LEN * 0.62) / (
            config.CHAIN_TOTAL_LEN * 0.38
        )
        tautness = max(0.0, min(1.0, tautness))

        pull_x = (
            solver_pull_x * config.CURSOR_PULL_GAIN
            + ball_momentum_x * config.BALL_CURSOR_DRAG * tautness
        )
        pull_y = (
            solver_pull_y * config.CURSOR_PULL_GAIN
            + ball_momentum_y * config.BALL_CURSOR_DRAG * tautness
        )
        pull_len = math.hypot(pull_x, pull_y)
        if pull_len > config.CURSOR_PULL_MAX:
            scale = config.CURSOR_PULL_MAX / pull_len
            pull_x *= scale
            pull_y *= scale
        cursor_node.x = sys_cx + pull_x
        cursor_node.y = sys_cy + pull_y

    ball = nodes[-1]
    impact = 0.0
    vx = ball.x - ball.px
    vy = ball.y - ball.py
    if ball.y > screen_h - config.BALL_RADIUS:
        ball.y = screen_h - config.BALL_RADIUS
        impact = max(impact, abs(vy))
        ball.py = ball.y + (ball.y - ball.py) * config.GROUND_BOUNCE
        ball.px = ball.x - (ball.x - ball.px) * config.GROUND_FRICTION
    if ball.x < config.BALL_RADIUS:
        ball.x = config.BALL_RADIUS
        impact = max(impact, abs(vx))
        ball.px = ball.x + (ball.x - ball.px) * config.WALL_BOUNCE
    if ball.x > screen_w - config.BALL_RADIUS:
        ball.x = screen_w - config.BALL_RADIUS
        impact = max(impact, abs(vx))
        ball.px = ball.x + (ball.x - ball.px) * config.WALL_BOUNCE
    if ball.y < config.BALL_RADIUS:
        ball.y = config.BALL_RADIUS
        impact = max(impact, abs(vy))
        ball.py = ball.y + (ball.y - ball.py) * config.WALL_BOUNCE

    return cursor_node.x, cursor_node.y, impact, pull_x, pull_y
