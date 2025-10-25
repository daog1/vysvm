# VyperSVM

Compile Vyper smart contracts to Solana SVM programs using LLVM IR.

## Overview

VyperSVM extends the Vyper programming language to target Solana's Virtual Machine (SVM) instead of Ethereum's EVM. It maintains Vyper's Python-like syntax while generating LLVM IR that compiles to Solana-compatible sBPF bytecode.

## Architecture

- **Frontend**: Vyper syntax analysis (unchanged)
- **Intermediate**: VENOM IR (Vyper's internal representation)
- **Backend**: LLVM IR generation for Solana
- **Output**: sBPF program deployable on Solana

## Installation

```bash
# Clone the repository
git clone <repository-url>
cd vysvm

# Install dependencies
pip install llvmlite

# Install LLVM tools
brew install llvm

# Install sbpf-linker
cargo install sbpf-linker
```

## Usage

### Compile a Vyper Contract

```bash
# Compile to LLVM IR
PYTHONPATH=vysvm python -m vyper.cli.vyper_compile contract.vy --experimental-codegen -f llvm > contract.ll

# Assemble to bitcode
llvm-as contract.ll -o contract.bc

# Link to sBPF
sbpf-linker contract.bc -o contract.so
```

### Build Script

```bash
chmod +x build_svm.sh
./build_svm.sh
```

## Example Contract

```vyper
# test.vy
@external
def add(a: uint256, b: uint256) -> uint256:
    return a + b
```

## Current Status

- ✅ Basic arithmetic operations (add, sub, mul)
- ✅ Solana entrypoint generation
- ✅ Syscall integration (sol_log_)
- ✅ LLVM IR output
- ✅ Control flow (jmp, jnz)
- ✅ Function calls
- 🔄 Storage operations

## Differences from EVM Vyper

- No gas costs
- Different storage model (accounts vs slots)
- Solana syscalls instead of opcodes
- sBPF instead of EVM bytecode

## Contributing

1. Modify code in `vysvm/` directory
2. Test with `build_svm.sh`
3. Deploy to Solana testnet for validation

## License

MIT
