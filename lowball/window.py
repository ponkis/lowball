import ctypes
from ctypes import wintypes, byref, sizeof, c_void_p, Structure, POINTER
import win32api
import win32con
import win32gui

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

LRESULT = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]
user32.DefWindowProcW.restype = LRESULT
user32.DispatchMessageW.argtypes = [POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT

WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_POPUP = 0x80000000
SW_SHOW = 5
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0


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

    _next_id = 0

    def __init__(self, width: int, height: int):
        self.w = width
        self.h = height
        self.class_name = f"LowballOverlayCls{LayeredOverlay._next_id}"
        LayeredOverlay._next_id += 1

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
        wc.lpszClassName = self.class_name
        wc.lpfnWndProc   = self._wndproc
        wc.hInstance     = win32api.GetModuleHandle(None)
        wc.hbrBackground = 0
        wc.style         = 0
        self.class_atom  = win32gui.RegisterClass(wc)

        self.hwnd = win32gui.CreateWindowEx(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST
            | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            self.class_atom,
            self.class_name,
            WS_POPUP,
            0, 0, width, height,
            0, 0, wc.hInstance, None,
        )
        win32gui.ShowWindow(self.hwnd, SW_SHOW)

        self.hdc_screen = user32.GetDC(0)
        self.hdc_mem    = gdi32.CreateCompatibleDC(self.hdc_screen)

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize        = sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth       = width
        bmi.bmiHeader.biHeight      = -height          
        bmi.bmiHeader.biPlanes      = 1
        bmi.bmiHeader.biBitCount    = 32
        bmi.bmiHeader.biCompression = BI_RGB

        self.p_bits = c_void_p()
        self.hbmp = gdi32.CreateDIBSection(
            self.hdc_mem, byref(bmi), 0, byref(self.p_bits), 0, 0,
        )
        self.old_obj = gdi32.SelectObject(self.hdc_mem, self.hbmp)

        self._byte_count = width * height * 4

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

    def pump(self) -> bool:
        """Drain the message queue. Returns False if WM_QUIT received."""
        msg = wintypes.MSG()
        while user32.PeekMessageW(byref(msg), 0, 0, 0, 1):  
            if msg.message == 0x0012:                      
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


def key_down(vk: int) -> bool:
    return bool(win32api.GetAsyncKeyState(vk) & 0x8000)
