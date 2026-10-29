#!/usr/bin/env python3
"""Run the complete CMM flow from an onboarding form.

Usage: python3 run_flow.py [form.json] [out_dir]
Defaults: demo/form_alex.json -> examples_out/flow/
Requires the Meta Model API key provider (see attested/meta_client.py).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from attested import flow  # noqa: E402

if __name__ == "__main__":
    form = sys.argv[1] if len(sys.argv) > 1 else "demo/form_alex.json"
    out = sys.argv[2] if len(sys.argv) > 2 else "examples_out/flow"
    result = flow.run(form, out)
    print(f"\nDone: {result['judged']} matches judged, "
          f"{len(result['built'])} dossier(s) built.")
