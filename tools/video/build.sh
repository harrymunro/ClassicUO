#!/bin/bash
# Build the window recorder (wincaps) into tools/video/bin/. Needs the Xcode command line tools.
set -e
cd "$(dirname "$0")"
mkdir -p bin
swiftc -O wincaps/main.swift -o bin/wincaps
echo "built $(pwd)/bin/wincaps"
