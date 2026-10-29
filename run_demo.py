#!/usr/bin/env python3
"""Run the Attested demo pipeline end to end."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from attested import pipeline  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jd", default="demo/jd_snowpark.md")
    parser.add_argument("--profile", default="demo/profile_attested.md")
    parser.add_argument("--out", default="examples_out")
    args = parser.parse_args()
    pipeline.run(args.jd, args.profile, args.out)


if __name__ == "__main__":
    main()
