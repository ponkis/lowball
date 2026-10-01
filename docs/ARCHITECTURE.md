# Architecture

Lowball is a small, single-process Windows desktop simulation. Pygame and NumPy handle the frame surface and procedural rendering; a native Win32 layered window displays the transparent, click-through overlay. The chain is a short Verlet integration simulation with a constraint solver.

## Runtime flow

~~~
main.py / run.py / python -m lowball
                 |
                 v
               Engine
          /      |      \
         /       |       \
resources    entities   audio
    |            |         |
  assets       chain     samples
                 \         /
                  renderer
                     |
             transparent canvas
                     |
          premultiplied BGRA frame
                     |
             Win32 layered window
~~~

1. The root launcher or package entry point calls lowball.engine.main(), which creates an Engine.
2. The engine sets process DPI awareness, reads the desktop dimensions, initializes Pygame, and creates the transparent render surface.
3. The native window module creates a topmost, no-activate, click-through layered window and its backing bitmap.
4. The engine resolves the sound samples, initializes the ball renderer and chain links, and builds the chain at the current cursor location.
5. Each frame pumps native window messages, reads cursor and key state, steps the chain physics, updates the cursor position, and renders a bounded frame around the chain.
6. The rendered surface is converted to premultiplied BGRA pixels and sent to UpdateLayeredWindow.
7. Leaving the loop closes Win32 resources and shuts down Pygame.

## Modules

### lowball.engine

Owns application lifetime, DPI and display sizing, the frame loop, cursor integration, and coordination between simulation, rendering, audio, and the overlay.

### lowball.entities.chain

Stores the chain nodes and performs Verlet integration, distance constraints, cursor pull, and collisions with screen boundaries. The engine supplies input and desktop dimensions.

### lowball.renderer

Builds and shades the metal ball and chain links, composes each frame, selects the overlay frame origin, and converts Pygame pixels into premultiplied BGRA.

### lowball.window

Creates and updates the native click-through layered window, pumps Windows messages, and reads key state. It owns the Win32 structures and handles used to display frames.

### lowball.audio

Loads and pitch-shifts the chain and impact samples, then selects effects based on motion and collision intensity.

### lowball.resources

Resolves asset paths beside the Python package or inside PyInstaller's temporary bundle directory.

### lowball.config

Contains simulation, audio, frame, and keyboard constants. It has no runtime state or user settings.

### Compatibility entry points

lowball.main and lowball.physics re-export the existing main() and chain simulation APIs so older imports keep working while the implementation follows the engine and entities layout.

## Assets and packaging

Runtime audio lives in lowball/assets. The Python package metadata includes MP3 files for package builds, while lowball.spec includes them in a standalone Windows executable. Keep both asset rules aligned when adding another asset type.

## Design constraints

- The overlay must remain topmost, transparent, and click-through, without activating or intercepting input from windows below it.
- The simulation updates the actual system cursor to match the resolved chain state.
- The off-screen render surface is fixed-size and follows the cursor and ball; it is not a screenshot-sized display surface.
- Sound loading is optional at runtime; missing or unsupported audio disables sound without stopping the simulation.
- The package entry point and root launchers must continue to work.
