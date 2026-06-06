# lowball

**lowball** is a lightweight, high-performance desktop interactive accessory for Windows that attaches a simulated heavy metal ball and chain directly to your cursor. It runs on a completely transparent, click-through desktop overlay.

Designed and developed by **ponkis**.

---

## Features

* **Click-Through Layered Overlay**: Uses Windows native Desktop Window Manager (DWM) APIs (`WS_EX_LAYERED`, `WS_EX_TRANSPARENT`, `WS_EX_NOACTIVATE`) to render frames directly onto your screen without intercepting mouse clicks, ensuring you can still click desktop icons or windows underneath.
* **Verlet Integration Physics**: The chain is simulated in real time using a multi-substep Verlet integration constraint solver. It supports tension, gravity, boundaries, and friction against the edges of the screen.
* **3D sphere Ray-Casting**: The heavy iron ball is procedurally shaded in real-time, featuring realistic specular reflection (soft/hard Blinn-Phong highlights), rim lighting, and a rolling surface texture that responds dynamically to the ball's rotational velocity (yaw/pitch).
* **Dynamic Audio Synthesis**: Features procedurally pitch-shifted sound effects for chain movement and impact collisions. Volume and pitch shift are computed based on collision velocity and kinetic tension.

---

## Controls

* **Cursor Movement**: Pull the heavy ball around your screen. The faster you move, the more weight it carries.
* **Left / Right Control**: Hold down `Ctrl` to lock the cursor's anchor position, allowing you to swing the ball in circular orbits.
* **Escape (`ESC`)**: Immediately exit the program.
* **Ctrl + Shift + Q**: Alternative shortcut to exit the program.

---

## Installation & Setup

lowball requires **Python 3.9 or higher** on **Windows**.

1. **Clone the repository**:
   ```bash
   git clone https://github.com/ponkis/lowball.git
   cd lowball
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
   Or install the project as an editable package:
   ```bash
   pip install -e .
   ```

---

## Running lowball

To run the program, use the root execution script:
```bash
python run.py
```

Or execute it as a module:
```bash
python -m lowball
```

If you installed the package via `pip install .`, you can launch it from anywhere using the CLI entrypoint:
```bash
lowball
```
