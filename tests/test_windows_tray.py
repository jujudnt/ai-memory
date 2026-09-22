import sys

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows tray dependencies are platform-specific")
def test_tray_mark_is_nonempty_and_transparent():
    from PIL import Image, ImageDraw
    from aimemory.windows_tray import _tray_image

    icon = _tray_image(Image, ImageDraw, "#16835b")

    assert icon.size == (64, 64)
    assert icon.getpixel((0, 0))[3] == 0
    assert icon.getbbox() == (5, 5, 60, 60)
