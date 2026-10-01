<div align="center">

# lowball

A physics-based ball-and-chain cursor accessory for Windows.

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Pygame](https://img.shields.io/badge/pygame-2.x-0D8F45)](https://www.pygame.org/)
[![Windows](https://img.shields.io/badge/platform-Windows-0078D4?logo=windows&logoColor=white)](https://www.microsoft.com/windows)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

</div>

Lowball renders a heavy metal ball and chain around the cursor in a transparent, click-through desktop overlay. It is a small native Windows application built with Pygame, NumPy, and the Windows API.

## Features

- Click-through layered overlay that leaves desktop and window input available underneath.
- Verlet integration with constraint solving, gravity, collisions, and cursor pull.
- Procedural metal shading and a rotating surface texture for the ball and chain links.
- Pitch-shifted chain and impact sounds selected in response to motion.
- Asset lookup that works from source and from a PyInstaller executable.
- A compact package split by runtime responsibility.

## Architecture

~~~
.
├── .github/                 # Issue and pull request templates
├── docs/                    # Architecture and configuration notes
├── main.py                  # Root launcher
├── lowball.spec             # PyInstaller build specification
├── pyproject.toml            # Package metadata and console entry point
├── requirements.txt          # Runtime dependencies
├── lowball/
│   ├── __init__.py           # Version and author metadata
│   ├── __main__.py           # Package execution entry point
│   ├── audio.py              # Motion-driven sound effects
│   ├── config.py             # Simulation and window constants
│   ├── engine.py             # Application lifetime and frame loop
│   ├── main.py               # Compatibility entry point
│   ├── physics.py            # Compatibility exports
│   ├── renderer.py           # Ball, link, and overlay rendering
│   ├── resources.py          # Source and bundled asset paths
│   ├── window.py             # Native click-through Windows overlay
│   ├── assets/               # Chain and impact sound samples
│   └── entities/
│       ├── __init__.py
│       └── chain.py           # Chain node state and physics solver
└── run.py                    # Existing launcher, kept for compatibility
~~~

See [Architecture](docs/ARCHITECTURE.md) for the runtime flow and [Configuration](docs/CONFIGURATION.md) for tuning values.

## Requirements

- Python 3.9 or later
- Windows
- Pygame 2.5 or later
- NumPy 1.20 or later
- pywin32 300 or later

## Installation

1. Clone the repository:

   ~~~bash
   git clone https://github.com/ponkis/lowball.git
   cd lowball
   ~~~

2. Create and activate a virtual environment:

   ~~~bash
   python -m venv .venv
   .venv\Scripts\activate
   ~~~

3. Install the runtime dependencies:

   ~~~bash
   python -m pip install -r requirements.txt
   ~~~

4. For an editable package installation with the console command:

   ~~~bash
   python -m pip install -e .
   ~~~

## Running Lowball

Start the application using any of these entry points:

- Root launcher: python main.py
- Existing launcher: python run.py
- Package module: python -m lowball
- Installed command: lowball

## Controls

| Input | Action |
| --- | --- |
| Move the cursor | Pull the ball and chain around the screen |
| Hold left or right Ctrl | Lock the cursor anchor and swing the ball |
| Esc | Exit |
| Ctrl + Shift + Q | Alternative exit shortcut |

The overlay is transparent and click-through. You can continue interacting with the desktop beneath it while Lowball runs.

## Building a standalone executable

Install PyInstaller and build from the project root:

~~~
python -m pip install pyinstaller
pyinstaller --clean lowball.spec
~~~

The standalone application is written to dist/lowball.exe; the chain and impact sounds are bundled with it.

## Contributing and security

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md), and follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

Lowball is available under the [MIT License](LICENSE).
