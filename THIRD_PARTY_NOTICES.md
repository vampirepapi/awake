# Third-party notices

Awake bundles the following third-party software in its packaged builds.

## Qt for Python (PySide6) and Qt 6 — LGPL-3.0

Awake uses Qt for Python (PySide6-Essentials) and the Qt 6 libraries under the
GNU Lesser General Public License v3.0 (https://www.gnu.org/licenses/lgpl-3.0.html).

* Awake does not modify Qt or PySide6.
* Packaged builds ship Qt and PySide6 as separate, dynamically loaded shared
  libraries (`.dylib` / `.dll` / `.so` files inside `Awake.app/Contents/Frameworks`
  or the `Awake\_internal` folder), so users can replace them with their own
  compatible builds.
* Source code for Qt: https://download.qt.io/official_releases/qt/
  Source code for Qt for Python: https://code.qt.io/cgit/pyside/pyside-setup.git/
* The complete LGPL-3.0 and GPL-3.0 texts are included with the PySide6 wheel and
  in the packaged application's PySide6 directory.

## Python — PSF License Agreement

The packaged application embeds the CPython interpreter, licensed under the
Python Software Foundation License (https://docs.python.org/3/license.html).

## Lucide icons — ISC License

ISC License

Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part of Feather (MIT). All other copyright (c) for Lucide are held by Lucide Contributors 2022.

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
