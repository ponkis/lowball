# Configuration

Lowball has no user settings file. Its behavior is tuned through constants in lowball/config.py; restart the application after changing them.

## Physics and chain

| Constant | Default | Purpose |
| --- | ---: | --- |
| CHAIN_TOTAL_LEN | 170.0 | Total rest length of the chain |
| NUM_LINKS | 14 | Number of solver segments |
| LINK_LEN | derived | Rest length of one segment |
| BALL_RADIUS | 32 | Ball radius in pixels |
| BALL_MASS | 24.0 | Ball mass relative to chain links |
| LINK_MASS | 0.22 | Mass of each intermediate chain node |
| GRAVITY | 0.85 | Downward acceleration per simulation step |
| CONSTRAINT_ITERS | 14 | Distance constraint solver iterations per step |
| SUBSTEPS | 2 | Physics steps per rendered frame |
| AIR_DAMP_PER_SEC | 0.82 | Per-second velocity retention used to derive AIR_DAMP |
| AIR_DAMP | derived | Per-step velocity retention |

Cursor response is controlled by CURSOR_MASS_BASE, CURSOR_SPEED_REF, CURSOR_MIN_MASS, CURSOR_PULL_GAIN, CURSOR_PULL_MAX, and BALL_CURSOR_DRAG. The ball spin response is controlled by BALL_SPIN_RESPONSE.

## Collisions

| Constant | Default | Purpose |
| --- | ---: | --- |
| GROUND_BOUNCE | 0.1 | Vertical velocity retained after a floor impact |
| GROUND_FRICTION | 0.94 | Horizontal velocity retained after a floor impact |
| WALL_BOUNCE | 0.55 | Velocity retained after a wall or ceiling impact |
| BALL_RADIUS | 32 | Clearance from each screen edge |

## Frame and window

| Constant | Default | Purpose |
| --- | ---: | --- |
| FPS | 100 | Target frame rate for the main loop |
| FRAME_W | 384 | Width of the transparent render surface |
| FRAME_H | 384 | Height of the transparent render surface |
| EXIT_VK | Escape | Immediate exit key |
| QUIT_VKS | Ctrl, Shift, Q | Alternative exit key chord |
| VK_LCONTROL / VK_RCONTROL | Windows key codes | Cursor anchor locking |

The frame dimensions bound rendering work, while compute_frame_origin() keeps the cursor and ball in view. The overlay uses native screen coordinates and adapts to the active desktop dimensions.

## Audio

| Constant | Default | Purpose |
| --- | ---: | --- |
| DROP_IMPACT_MIN | 3.2 | Minimum collision intensity for an impact sound |
| DROP_SOUND_COOLDOWN | 130 ms | Minimum time between impact sounds |
| CHAIN_SOUND_MIN | 0.65 | Minimum average chain movement for a chain sound |
| CHAIN_SOUND_COOLDOWN | 75 ms | Minimum time between chain sounds |

Larger constraint counts and substep counts increase simulation work. Higher frame rates, heavier surface rendering, and frequent sounds also increase CPU or audio work. Change one group at a time and test cursor feel, screen-edge collisions, and click-through behavior on Windows.
