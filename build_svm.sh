#!/bin/bash

# Build Vyper contract for Solana SVM

set -e

# Install dependencies if needed
pip install llvmlite

# Compile Vyper to LLVM IR
PYTHONPATH=. python -m vyper.cli.vyper_compile test.vy --experimental-codegen -f llvm > program.ll

# Assemble to bitcode
llvm-as program.ll -o program.bc

# Link to sBPF (explicit CPU + exported entrypoint to keep code section)
sbpf-linker --cpu v3 --export entrypoint program.bc -o hello_world.so

echo "Built hello_world.so"
