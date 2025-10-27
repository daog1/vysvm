"""VENOM to LLVM IR translator for Solana SVM."""

from __future__ import annotations

from dataclasses import dataclass

import llvmlite.binding as llvm
import llvmlite.ir as ir

from vyper.venom.basicblock import IRBasicBlock, IRInstruction, IRLabel, IRLiteral, IRVariable
from vyper.venom.context import IRContext
from vyper.venom.function import IRFunction


@dataclass(frozen=True)
class MemoryRef:
    """Reference to a memory object with a byte offset."""

    mem_id: int
    offset: int


class MemoryObject:
    """Simple byte-addressable memory buffer used for debug evaluation."""

    def __init__(self, size: int) -> None:
        self.data = bytearray(size)

    def ensure_capacity(self, end: int) -> None:
        if end > len(self.data):
            self.data.extend(b"\x00" * (end - len(self.data)))

    def write(self, offset: int, buf: bytes) -> None:
        end = offset + len(buf)
        self.ensure_capacity(end)
        self.data[offset:end] = buf

    def read(self, offset: int, length: int) -> bytes:
        end = offset + length
        self.ensure_capacity(end)
        return bytes(self.data[offset:end])


class VenomToLLVM:
    def __init__(self, ctx: IRContext):
        self.ctx = ctx
        self.module = ir.Module(name="solana_program")
        self.module.triple = "bpf"
        self.builder = None
        self.current_function = None
        self.variable_map = {}  # VENOM var -> LLVM value
        self.label_map = {}  # VENOM label -> LLVM block
        self.function_map = {}
        self.log_counter = 0
        self.abstract_values: dict[IRVariable, object] = {}
        self.memory_objects: dict[int, MemoryObject] = {}
        self._memory_counter = 0

    def generate_llvm_ir(self) -> str:
        # Translate VENOM functions
        for fn in self.ctx.functions.values():
            self._translate_function(fn)

        # Create Solana entrypoint
        self._create_entrypoint()

        # Verify and return
        llvm_module = llvm.parse_assembly(str(self.module))
        llvm_module.verify()
        return str(self.module)

    def _create_entrypoint(self):
        # Solana entrypoint: uint64_t entrypoint(uint8_t *input)
        i8 = ir.IntType(8)
        i64 = ir.IntType(64)
        i8_ptr = i8.as_pointer()

        entrypoint_type = ir.FunctionType(i64, [i8_ptr])
        entrypoint_func = ir.Function(self.module, entrypoint_type, name="entrypoint")
        entrypoint_func.linkage = 'external'

        block = entrypoint_func.append_basic_block(name="entry")
        self.builder = ir.IRBuilder(block)

        target_fn = None
        if self.ctx.entry_function is not None:
            target_fn = self.function_map.get(self.ctx.entry_function.name.value)
        if target_fn is None:
            target_fn = self.function_map.get("__main_entry")

        if target_fn is not None:
            result = self.builder.call(target_fn, [])
            if isinstance(result.type, ir.IntType) and result.type.width == 64:
                self.builder.ret(result)
            else:
                self.builder.ret(ir.Constant(i64, 0))
        else:
            self.builder.ret(ir.Constant(i64, 0))

    def _translate_function(self, fn: IRFunction):
        # Collect params
        self.abstract_values.clear()
        self.memory_objects.clear()
        self._memory_counter = 0

        params = []
        for bb in fn.get_basic_blocks():
            for inst in bb.instructions:
                if inst.opcode == "param":
                    params.append(inst.output)

        # Create LLVM function
        i64 = ir.IntType(64)
        func_type = ir.FunctionType(i64, [i64] * len(params))
        llvm_func = ir.Function(self.module, func_type, name=fn.name.value)
        self.current_function = llvm_func
        self.function_map[fn.name.value] = llvm_func

        # Map params to variables
        for i, param_var in enumerate(params):
            self.variable_map[param_var] = llvm_func.args[i]

        # Translate basic blocks
        for bb in fn.get_basic_blocks():
            llvm_bb = llvm_func.append_basic_block(name=bb.label.value)
            self.label_map[bb.label] = llvm_bb

        # Translate instructions
        for bb in fn.get_basic_blocks():
            self.builder = ir.IRBuilder(self.label_map[bb.label])
            for inst in bb.instructions:
                self._simulate_instruction(inst)
                self._translate_instruction(inst)

    def _translate_instruction(self, inst: IRInstruction):
        opcode = inst.opcode

        if opcode == "add":
            self._emit_binary_op("add", inst)
        elif opcode == "sub":
            self._emit_binary_op("sub", inst)
        elif opcode == "mul":
            self._emit_binary_op("mul", inst)
        elif opcode == "return":
            self.builder.ret(ir.Constant(ir.IntType(64), 0))
        elif opcode == "jmp":
            target = inst.operands[0]
            self.builder.branch(self.label_map[target])
        elif opcode == "jnz":
            cond = self._get_operand_value(inst.operands[0])
            true_label = inst.operands[1]
            false_label = inst.operands[2]
            self.builder.cbranch(cond, self.label_map[true_label], self.label_map[false_label])
        elif opcode == "invoke":
            target = inst.operands[0]
            # Assume no args for now
            result = self.builder.call(self.label_map[target], [])
            if inst.output:
                self.variable_map[inst.output] = result
        elif opcode == "log":
            message = self._extract_log_message(inst)
            if message is None:
                message = self._recover_log_from_memory(inst)
            if message is None:
                message = inst.annotation or "Vyper log"
            self._emit_sol_log(message)
        # Add more instructions...

    def _simulate_instruction(self, inst: IRInstruction) -> None:
        """Lightweight evaluation of memory operations for debug logging."""

        opcode = inst.opcode

        if opcode in {"alloca", "palloca", "calloca"}:
            size = self._get_literal(inst.operands[1]) if len(inst.operands) > 1 else 0
            mem = self._new_memory(size)
            if inst.output is not None:
                self.abstract_values[inst.output] = mem
        elif opcode == "assign":
            if inst.output is not None:
                val = self._get_operand_abstract(inst.operands[0])
                self.abstract_values[inst.output] = self._clone_abstract(val)
        elif opcode == "add":
            if inst.output is not None:
                left = self._get_operand_abstract(inst.operands[0])
                right = self._get_operand_abstract(inst.operands[1])
                result = self._add_abstract(left, right)
                self.abstract_values[inst.output] = result
        elif opcode == "sub":
            if inst.output is not None:
                left = self._as_int(self._get_operand_abstract(inst.operands[0]))
                right = self._as_int(self._get_operand_abstract(inst.operands[1]))
                self.abstract_values[inst.output] = left - right
        elif opcode == "and":
            if inst.output is not None:
                left = self._as_int(self._get_operand_abstract(inst.operands[0]))
                right = self._as_int(self._get_operand_abstract(inst.operands[1]))
                self.abstract_values[inst.output] = left & right
        elif opcode == "or":
            if inst.output is not None:
                left = self._as_int(self._get_operand_abstract(inst.operands[0]))
                right = self._as_int(self._get_operand_abstract(inst.operands[1]))
                self.abstract_values[inst.output] = left | right
        elif opcode == "xor":
            if inst.output is not None:
                left = self._as_int(self._get_operand_abstract(inst.operands[0]))
                right = self._as_int(self._get_operand_abstract(inst.operands[1]))
                self.abstract_values[inst.output] = left ^ right
        elif opcode == "shr":
            if inst.output is not None:
                value = self._as_int(self._get_operand_abstract(inst.operands[0]))
                shift = self._as_int(self._get_operand_abstract(inst.operands[1]))
                self.abstract_values[inst.output] = value >> shift
        elif opcode == "iszero":
            if inst.output is not None:
                val = self._as_int(self._get_operand_abstract(inst.operands[0]))
                self.abstract_values[inst.output] = 1 if val == 0 else 0
        elif opcode == "calldatasize":
            if inst.output is not None:
                self.abstract_values[inst.output] = 0
        elif opcode == "mstore":
            ptr = self._get_operand_abstract(inst.operands[1])
            data = self._get_operand_abstract(inst.operands[0])
            if isinstance(ptr, MemoryRef):
                self._memory_write(ptr, data, 32)
            elif isinstance(ptr, int):
                self._memory_write(ptr, data, 32)
        elif opcode == "mload":
            if inst.output is not None:
                ptr = self._get_operand_abstract(inst.operands[0])
                if isinstance(ptr, MemoryRef) or isinstance(ptr, int):
                    word = self._memory_read(ptr, 32)
                    value = int.from_bytes(word, "big")
                    self.abstract_values[inst.output] = value
        elif opcode == "mcopy":
            length = self._as_int(self._get_operand_abstract(inst.operands[0]))
            src = self._get_operand_abstract(inst.operands[1])
            dest = self._get_operand_abstract(inst.operands[2])
            if isinstance(dest, MemoryRef) and isinstance(src, MemoryRef):
                data = self._memory_read(src, length)
                self._memory_write(dest, data, length)
            elif isinstance(dest, MemoryRef) and isinstance(src, int):
                src_ref = MemoryRef(0, src)
                data = self._memory_read(src_ref, length)
                self._memory_write(dest, data, length)
            elif isinstance(dest, int) and isinstance(src, MemoryRef):
                dest_ref = MemoryRef(0, dest)
                data = self._memory_read(src, length)
                self._memory_write(dest_ref, data, length)
            elif isinstance(dest, int) and isinstance(src, int):
                dest_ref = MemoryRef(0, dest)
                src_ref = MemoryRef(0, src)
                data = self._memory_read(src_ref, length)
                self._memory_write(dest_ref, data, length)
        elif opcode == "calldatacopy":
            dest = self._get_operand_abstract(inst.operands[0])
            length = self._as_int(self._get_operand_abstract(inst.operands[2]))
            if isinstance(dest, MemoryRef):
                self._memory_write(dest, b"\x00" * length, length)

    def _new_memory(self, size: int) -> MemoryRef:
        self._memory_counter += 1
        mem_id = self._memory_counter
        self.memory_objects[mem_id] = MemoryObject(size)
        return MemoryRef(mem_id, 0)

    def _clone_abstract(self, value: object) -> object:
        if isinstance(value, MemoryRef):
            return MemoryRef(value.mem_id, value.offset)
        return value

    def _add_abstract(self, left: object, right: object) -> object:
        if isinstance(left, MemoryRef) and isinstance(right, int):
            return MemoryRef(left.mem_id, left.offset + right)
        if isinstance(right, MemoryRef) and isinstance(left, int):
            return MemoryRef(right.mem_id, right.offset + left)
        return self._as_int(left) + self._as_int(right)

    def _get_operand_abstract(self, operand) -> object:
        if isinstance(operand, IRVariable):
            return self.abstract_values.get(operand)
        if isinstance(operand, IRLiteral):
            return operand.value
        return None

    def _get_literal(self, operand) -> int:
        if isinstance(operand, IRLiteral):
            return operand.value
        return 0

    def _as_int(self, value: object) -> int:
        if isinstance(value, MemoryRef):
            raise TypeError("Cannot treat MemoryRef as int")
        if value is None:
            return 0
        return int(value)

    def _memory_write(self, ref: MemoryRef, data: object, size: int) -> None:
        if isinstance(ref, int):
            ref = MemoryRef(0, ref)

        mem = self.memory_objects.get(ref.mem_id)
        if mem is None:
            mem = MemoryObject(ref.offset + size)
            self.memory_objects[ref.mem_id] = mem

        if isinstance(data, MemoryRef):
            buf = self._memory_read(data, size)
        elif isinstance(data, int):
            sign = data < 0
            try:
                buf = data.to_bytes(size, "big", signed=sign)
            except OverflowError:
                byte_len = (data.bit_length() + 7) // 8 or 1
                tmp = data.to_bytes(byte_len, "big", signed=sign)
                buf = tmp[-size:]
        elif isinstance(data, bytes):
            buf = data
        else:
            buf = b"\x00" * size

        if len(buf) < size:
            pad = b"\xff" if isinstance(data, int) and data < 0 else b"\x00"
            buf = buf.rjust(size, pad)
        elif len(buf) > size:
            buf = buf[-size:]

        mem.write(ref.offset, buf)

    def _memory_read(self, ref: MemoryRef, length: int) -> bytes:
        if isinstance(ref, int):
            ref = MemoryRef(0, ref)

        mem = self.memory_objects.get(ref.mem_id)
        if mem is None:
            mem = MemoryObject(ref.offset + length)
            self.memory_objects[ref.mem_id] = mem
        return mem.read(ref.offset, length)

    def _recover_log_from_memory(self, inst: IRInstruction) -> str | None:
        # Expect operand layout: topics count literal, pointer, length
        operands = list(inst.operands)
        if len(operands) < 3:
            return None

        ptr_operand = operands[-1]
        length_operand = operands[-2]

        ptr_abs = self._get_operand_abstract(ptr_operand)
        length_abs = self._get_operand_abstract(length_operand)

        if isinstance(ptr_abs, int):
            ptr_abs = MemoryRef(0, ptr_abs)

        if not isinstance(ptr_abs, MemoryRef):
            return None

        try:
            length = self._as_int(length_abs)
        except TypeError:
            return None

        if length <= 0:
            return ""

        full = self._memory_read(ptr_abs, max(length, 32))
        if length <= len(full):
            head = full[:length]
            tail = full[-length:]
            if any(head):
                data = head
            elif any(tail):
                data = tail
            else:
                data = head
        else:
            data = self._memory_read(ptr_abs, length)
        try:
            message = data.decode("utf-8")
        except UnicodeDecodeError:
            message = data.hex()
        return message

    def _emit_binary_op(self, op: str, inst: IRInstruction):
        left = self._get_operand_value(inst.operands[0])
        right = self._get_operand_value(inst.operands[1])
        i64 = ir.IntType(64)

        result = None
        if op == "add":
            result = self.builder.add(left, right)
        elif op == "sub":
            result = self.builder.sub(left, right)
        elif op == "mul":
            result = self.builder.mul(left, right)

        if result is not None:
            self.variable_map[inst.output] = result

    def _get_operand_value(self, operand):
        if isinstance(operand, IRVariable):
            return self.variable_map.get(operand, ir.Constant(ir.IntType(64), 0))
        elif isinstance(operand, IRLiteral):
            return ir.Constant(ir.IntType(64), operand.value)
        # Handle labels, etc.

    def _emit_sol_log(self, message: str):
        # Implement sol_log_ syscall like in Pylana
        i8 = ir.IntType(8)
        i64 = ir.IntType(64)
        void = ir.VoidType()

        # Create string
        message_bytes = bytearray((message + "\x00").encode('utf-8'))
        c_message = ir.Constant(ir.ArrayType(i8, len(message_bytes)), message_bytes)
        name = f"log_message_{self.log_counter}"
        self.log_counter += 1
        global_message = ir.GlobalVariable(self.module, c_message.type, name=name)
        global_message.linkage = 'internal'
        global_message.global_constant = True
        global_message.initializer = c_message
        global_message.align = 1

        message_len = ir.Constant(i64, len(message))

        # sol_log_ syscall
        syscall_hash = ir.Constant(i64, 544561597)  # 0x207559bd
        func_ptr_type = ir.FunctionType(void, [i8.as_pointer(), i64])
        func_ptr_type_ptr = func_ptr_type.as_pointer()
        sol_log_ptr = self.builder.inttoptr(syscall_hash, func_ptr_type_ptr)

        string_ptr = self.builder.bitcast(global_message, i8.as_pointer())
        self.builder.call(sol_log_ptr, [string_ptr, message_len])

    def _extract_log_message(self, inst: IRInstruction):
        node = getattr(inst, "ast_source", None)
        if node is None:
            return None

        call = getattr(node, "value", None)
        if call is None:
            return None

        # Prefer keyword arguments (e.g. message="..."). Fallback to positional.
        keywords = getattr(call, "keywords", None) or []
        for kw in keywords:
            if getattr(kw, "arg", None) == "message":
                literal = getattr(kw, "value", None)
                value = getattr(literal, "value", None)
                if isinstance(value, str):
                    return value

        args = getattr(call, "args", None) or []
        if args:
            literal = args[0]
            value = getattr(literal, "value", None)
            if isinstance(value, str):
                return value

        return None
