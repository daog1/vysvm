"""
VENOM to LLVM IR translator for Solana SVM
"""

import llvmlite.ir as ir
import llvmlite.binding as llvm

from vyper.venom.basicblock import IRBasicBlock, IRInstruction, IRLabel, IRLiteral, IRVariable
from vyper.venom.context import IRContext
from vyper.venom.function import IRFunction


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
                message = inst.annotation or "Vyper log"
            self._emit_sol_log(message)
        # Add more instructions...

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
