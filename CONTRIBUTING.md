# Contributing

Thank you for helping improve Lowball. Small, focused changes are easiest to review.

## Set up a development environment

Lowball requires Windows, Python 3.9 or later, and the dependencies listed in requirements.txt.

~~~
git clone https://github.com/ponkis/lowball.git
cd lowball
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -e .
~~~

For executable packaging, install PyInstaller as a development dependency:

~~~
python -m pip install pyinstaller
~~~

## Before submitting a change

1. Keep simulation and input constants in lowball/config.py.
2. Keep application coordination in lowball/engine.py, chain state and physics in lowball/entities/, native window behavior in lowball/window.py, rendering in lowball/renderer.py, and sound behavior in lowball/audio.py.
3. Preserve the supported entry points: python main.py, python run.py, python -m lowball, and the installed lowball command.
4. If assets change, keep pyproject.toml and lowball.spec asset rules aligned.
5. Check syntax and package imports:

   ~~~
   python -m compileall -q main.py run.py lowball
   python -c "import lowball; import lowball.engine"
   ~~~

6. On Windows, run the app and check cursor motion, Ctrl anchor locking, screen-edge collisions, click-through behavior, Esc, Ctrl+Shift+Q, and window cleanup.
7. For packaging changes, build with pyinstaller --clean lowball.spec and launch dist/lowball.exe.

## Pull requests

- Explain the user-facing effect and motivation.
- Keep unrelated cleanup out of the change.
- Update the README or files in docs/ when controls, architecture, or configuration change.
- Include screenshots or recordings for visual changes when they make the result easier to review.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
