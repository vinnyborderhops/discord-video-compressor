# -*- mode: python ; coding: utf-8 -*-

import sys

sys.path.insert(0, SPECPATH)

from build_common import build_application

BUNDLE_FFMPEG = True
exe = build_application(bundle_ffmpeg=BUNDLE_FFMPEG)
