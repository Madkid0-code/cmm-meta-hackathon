#!/usr/bin/env python3
"""Serve the Attested Mission Control UI (stdlib only).

Usage: python3 run_web.py [port]
Reads the flow store in examples_out/flow and the demo fixture in demo/.
The Run buttons need META_MODEL_API_KEY in the environment (viewing the
real store works without it).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from attested import web  # noqa: E402

if __name__ == "__main__":
    base = os.path.dirname(os.path.abspath(__file__))
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    web.serve(
        out_dir=os.path.join(base, "examples_out/flow"),
        form_path=os.path.join(base, "demo/form_alex.json"),
        demo_dir=os.path.join(base, "demo"),
        fixture_out=os.path.join(base, "examples_out"),
        port=port,
    )
