import os
os.environ['FLAGS_cinn_new_group_scheduler'] = '1'
os.environ['FLAGS_group_schedule_tiling_first'] = '1'
os.environ['FLAGS_enable_pir_api'] = '1'
os.environ['FLAGS_cinn_bucket_compile'] = '1'
import sys
import unittest
import numpy as np
from dataclasses import dataclass
import typing as t

@dataclass
class Stage:
    name: str
    env_vars: t.Dict[str, str]

cinn_stages = [
    Stage(
        name="dynamic_to_static",
        env_vars=dict(
            PADDLE_DEBUG_ENABLE_CINN=False,
            FLAGS_prim_all=False,
            FLAGS_prim_enable_dynamic=False,
        ),
    ),
    Stage(
        name="prim",
        env_vars=dict(
            PADDLE_DEBUG_ENABLE_CINN=False,
            FLAGS_prim_all=True,
            FLAGS_prim_enable_dynamic=True,
        ),
    ),
    Stage(
        name="infer_symbolic",
        env_vars=dict(
            PADDLE_DEBUG_ENABLE_CINN=False,
            FLAGS_prim_all=True,
            FLAGS_prim_enable_dynamic=True,
            FLAGS_use_cinn=False,
            FLAGS_check_infer_symbolic=True,
        ),
    ),
	Stage(
        name="frontend",
        env_vars=dict(
            PADDLE_DEBUG_ENABLE_CINN=True,
            FLAGS_prim_all=True,
            FLAGS_prim_enable_dynamic=True,
            FLAGS_use_cinn=True,
            FLAGS_check_infer_symbolic=False,
            FLAGS_enable_fusion_fallback=True,
        ), 
    ),
    Stage(
        name="backend",
        env_vars=dict(
            PADDLE_DEBUG_ENABLE_CINN=True,
            FLAGS_prim_all=True,
            FLAGS_prim_enable_dynamic=True,
            FLAGS_use_cinn=True,
            FLAGS_check_infer_symbolic=False,
            FLAGS_enable_fusion_fallback=False,
        ), 
    ),
]

def GetCinnStageByName(name):
    for stage in cinn_stages:
        if stage.name == name:
            return stage
    return None

def GetCurrentCinnStage():
    name = os.getenv('PADDLE_DEBUG_CINN_STAGE_NAME')
    if name is None:
        return None
    stage_names = [stage.name for stage in cinn_stages]
    assert name in stage_names, (
        f"PADDLE_DEBUG_CINN_STAGE_NAME should be in {stage_names}"
    )
    return GetCinnStageByName(name)

def GetPrevCinnStage(stage):
    for i in range(1, len(cinn_stages)):
        if stage is cinn_stages[i]:
            return cinn_stages[i - 1]
    return None

def IsCinnStageEnableDiff():
    value = os.getenv('PADDLE_DEBUG_CINN_STAGE_ENABLE_DIFF')
    enabled = value in {
        '1',
        'true',
        'True',
    }
    if enabled:
        assert GetCurrentCinnStage() is not None
    return enabled

def GetExitCodeAndStdErr(cmd, env):
    env = {
        k:v
        for k, v in env.items()
        if v is not None
    }
    import subprocess
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    return result.returncode, result.stderr

def GetStageExitCodeAndStdErr(stage):
    return GetExitCodeAndStdErr(
        [sys.executable, __file__],
        env=dict(
            PADDLE_DEBUG_CINN_STAGE_NAME=stage.name,
            PADDLE_DEBUG_CINN_STAGE_ENABLE_DIFF='0',
            PYTHONPATH=os.getenv('PYTHONPATH'),
            ATHENA_ENABLE_TRY_RUN="False",
        ),
    )

def AthenaTryRunEnabled():
    return os.getenv('ATHENA_ENABLE_TRY_RUN') not in {
        "0",
        "False",
        "false",
        "OFF"
    }

def GetNeedSkipAndSkipMessage():
    current_stage = GetCurrentCinnStage()
    assert current_stage is not None
    if not IsCinnStageEnableDiff():
        return False, ""
    last_stage = GetPrevCinnStage(current_stage)
    if last_stage is None:
        return False, ""
    exitcode, stderr = GetStageExitCodeAndStdErr(last_stage)
    if exitcode != 0:
        return True, f"last stage failed."
    return False, ""

def GetCurrentStageTryRunExitCodeAndStdErr():
    if not AthenaTryRunEnabled():
        return False, ""
    current_stage = GetCurrentCinnStage()
    assert current_stage is not None
    return GetStageExitCodeAndStdErr(current_stage)

def SetDefaultEnv(**env_var2value):
    for env_var, value in env_var2value.items():
        if os.getenv(env_var) is None:
            os.environ[env_var] = str(value)

SetDefaultEnv(
    PADDLE_DEBUG_CINN_STAGE_NAME="backend",
    PADDLE_DEBUG_CINN_STAGE_ENABLE_DIFF=False,
    PADDLE_DEBUG_ENABLE_CINN=True,
    FLAGS_enable_pir_api=True,
    FLAGS_prim_all=True,
    FLAGS_prim_enable_dynamic=True,
    FLAGS_use_cinn=False,
    FLAGS_check_infer_symbolic=False,
    FLAGS_enable_fusion_fallback=False,
)

need_skip, skip_message = GetNeedSkipAndSkipMessage()
try_run_exit_code, try_run_stderr = GetCurrentStageTryRunExitCodeAndStdErr()
class TestTryRun(unittest.TestCase):
    def test_panic(self):
        if not AthenaTryRunEnabled():
            return
        if try_run_exit_code == 0:
            # All unittest cases passed.
            return
        if try_run_exit_code > 0:
            # program failed but not panic.
            return
        # program panicked.
        kOutputLimit = 65536
        message = try_run_stderr[-kOutputLimit:]
        raise RuntimeError(f"panicked. last {kOutputLimit} characters of stderr: \n{message}")

import paddle

def SetEnvVar(env_var2value):
    for env_var, value in env_var2value.items():
        os.environ[env_var] = str(value)
    paddle.set_flags({
        env_var:value
        for env_var, value in env_var2value.items()
        if env_var.startswith('FLAGS_')
    })

if GetCurrentCinnStage() is not None:
    SetEnvVar(GetCurrentCinnStage().env_vars)

def NumOperationsInBlock(block_idx):
    return [526][block_idx] - 1 # number-of-ops-in-block

def GetPaddleDebugNumAllowedOps():
    try:
        return int(os.getenv('PADDLE_DEBUG_NUM_ALLOWED_OPS'))
    except:
        return None

paddle_debug_num_allowed_ops = GetPaddleDebugNumAllowedOps()


if type(paddle_debug_num_allowed_ops) is not int:
    def EarlyReturn(block_idx, op_idx):
        return False      
else:
    def EarlyReturn(block_idx, op_idx):
        return op_idx >= paddle_debug_num_allowed_ops

class BlockEntries:
    def builtin_module_815_0_0(self, parameter_0, parameter_4, parameter_1, parameter_3, parameter_2, parameter_5, parameter_9, parameter_6, parameter_8, parameter_7, parameter_10, parameter_14, parameter_11, parameter_13, parameter_12, parameter_15, parameter_19, parameter_16, parameter_18, parameter_17, parameter_20, parameter_24, parameter_21, parameter_23, parameter_22, parameter_25, parameter_29, parameter_26, parameter_28, parameter_27, parameter_30, parameter_34, parameter_31, parameter_33, parameter_32, parameter_35, parameter_39, parameter_36, parameter_38, parameter_37, parameter_40, parameter_44, parameter_41, parameter_43, parameter_42, parameter_45, parameter_49, parameter_46, parameter_48, parameter_47, parameter_50, parameter_54, parameter_51, parameter_53, parameter_52, parameter_55, parameter_59, parameter_56, parameter_58, parameter_57, parameter_60, parameter_64, parameter_61, parameter_63, parameter_62, parameter_65, parameter_69, parameter_66, parameter_68, parameter_67, parameter_70, parameter_74, parameter_71, parameter_73, parameter_72, parameter_75, parameter_79, parameter_76, parameter_78, parameter_77, parameter_80, parameter_84, parameter_81, parameter_83, parameter_82, parameter_85, parameter_89, parameter_86, parameter_88, parameter_87, parameter_90, parameter_94, parameter_91, parameter_93, parameter_92, parameter_95, parameter_99, parameter_96, parameter_98, parameter_97, parameter_100, parameter_104, parameter_101, parameter_103, parameter_102, parameter_105, parameter_109, parameter_106, parameter_108, parameter_107, parameter_110, parameter_114, parameter_111, parameter_113, parameter_112, parameter_115, parameter_119, parameter_116, parameter_118, parameter_117, parameter_120, parameter_124, parameter_121, parameter_123, parameter_122, parameter_125, parameter_129, parameter_126, parameter_128, parameter_127, parameter_130, parameter_134, parameter_131, parameter_133, parameter_132, parameter_135, parameter_139, parameter_136, parameter_138, parameter_137, parameter_140, parameter_144, parameter_141, parameter_143, parameter_142, parameter_145, parameter_149, parameter_146, parameter_148, parameter_147, parameter_150, parameter_154, parameter_151, parameter_153, parameter_152, parameter_155, parameter_159, parameter_156, parameter_158, parameter_157, parameter_160, parameter_164, parameter_161, parameter_163, parameter_162, parameter_165, parameter_169, parameter_166, parameter_168, parameter_167, parameter_170, parameter_174, parameter_171, parameter_173, parameter_172, parameter_175, parameter_179, parameter_176, parameter_178, parameter_177, parameter_180, parameter_184, parameter_181, parameter_183, parameter_182, parameter_185, parameter_189, parameter_186, parameter_188, parameter_187, parameter_190, parameter_194, parameter_191, parameter_193, parameter_192, parameter_195, parameter_199, parameter_196, parameter_198, parameter_197, parameter_200, parameter_204, parameter_201, parameter_203, parameter_202, parameter_205, parameter_209, parameter_206, parameter_208, parameter_207, parameter_210, parameter_214, parameter_211, parameter_213, parameter_212, parameter_215, parameter_219, parameter_216, parameter_218, parameter_217, parameter_220, parameter_224, parameter_221, parameter_223, parameter_222, parameter_225, parameter_229, parameter_226, parameter_228, parameter_227, parameter_230, parameter_234, parameter_231, parameter_233, parameter_232, parameter_235, parameter_239, parameter_236, parameter_238, parameter_237, parameter_240, parameter_244, parameter_241, parameter_243, parameter_242, parameter_245, parameter_249, parameter_246, parameter_248, parameter_247, parameter_250, parameter_254, parameter_251, parameter_253, parameter_252, parameter_255, parameter_259, parameter_256, parameter_258, parameter_257, parameter_260, parameter_264, parameter_261, parameter_263, parameter_262, parameter_265, parameter_269, parameter_266, parameter_268, parameter_267, parameter_270, parameter_274, parameter_271, parameter_273, parameter_272, parameter_275, parameter_279, parameter_276, parameter_278, parameter_277, parameter_280, parameter_281, feed_0):

        # pd_op.conv2d: (-1x24x112x112xf32) <- (-1x3x224x224xf32, 24x3x3x3xf32)
        conv2d_0 = paddle._C_ops.conv2d(feed_0, parameter_0, [2, 2], [1, 1], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x112x112xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x112x112xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__0, batch_norm__1, batch_norm__2, batch_norm__3, batch_norm__4, batch_norm__5 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_0, parameter_1, parameter_2, parameter_3, parameter_4, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x112x112xf32) <- (-1x24x112x112xf32)
        relu__0 = paddle._C_ops.relu_(batch_norm__0)

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_0 = [3, 3]

        # pd_op.pool2d: (-1x24x56x56xf32) <- (-1x24x112x112xf32, 2xi64)
        pool2d_0 = paddle._C_ops.pool2d(relu__0, full_int_array_0, [2, 2], [1, 1], False, True, 'NCHW', 'max', False, False, 'EXPLICIT')

        # pd_op.depthwise_conv2d: (-1x24x28x28xf32) <- (-1x24x56x56xf32, 24x1x3x3xf32)
        depthwise_conv2d_0 = paddle._C_ops.depthwise_conv2d(pool2d_0, parameter_5, [2, 2], [1, 1], 'EXPLICIT', 24, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x24x28x28xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x28x28xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__6, batch_norm__7, batch_norm__8, batch_norm__9, batch_norm__10, batch_norm__11 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_0, parameter_6, parameter_7, parameter_8, parameter_9, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x12x28x28xf32) <- (-1x24x28x28xf32, 12x24x1x1xf32)
        conv2d_1 = paddle._C_ops.conv2d(batch_norm__6, parameter_10, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32, None) <- (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32)
        batch_norm__12, batch_norm__13, batch_norm__14, batch_norm__15, batch_norm__16, batch_norm__17 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_1, parameter_11, parameter_12, parameter_13, parameter_14, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x12x28x28xf32) <- (-1x12x28x28xf32)
        relu__1 = paddle._C_ops.relu_(batch_norm__12)

        # pd_op.conv2d: (-1x12x56x56xf32) <- (-1x24x56x56xf32, 12x24x1x1xf32)
        conv2d_2 = paddle._C_ops.conv2d(pool2d_0, parameter_15, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x12x56x56xf32, 12xf32, 12xf32, 12xf32, 12xf32, None) <- (-1x12x56x56xf32, 12xf32, 12xf32, 12xf32, 12xf32)
        batch_norm__18, batch_norm__19, batch_norm__20, batch_norm__21, batch_norm__22, batch_norm__23 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_2, parameter_16, parameter_17, parameter_18, parameter_19, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x12x56x56xf32) <- (-1x12x56x56xf32)
        relu__2 = paddle._C_ops.relu_(batch_norm__18)

        # pd_op.depthwise_conv2d: (-1x12x28x28xf32) <- (-1x12x56x56xf32, 12x1x3x3xf32)
        depthwise_conv2d_1 = paddle._C_ops.depthwise_conv2d(relu__2, parameter_20, [2, 2], [1, 1], 'EXPLICIT', 12, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32, None) <- (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32)
        batch_norm__24, batch_norm__25, batch_norm__26, batch_norm__27, batch_norm__28, batch_norm__29 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_1, parameter_21, parameter_22, parameter_23, parameter_24, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x12x28x28xf32) <- (-1x12x28x28xf32, 12x12x1x1xf32)
        conv2d_3 = paddle._C_ops.conv2d(batch_norm__24, parameter_25, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32, None) <- (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32)
        batch_norm__30, batch_norm__31, batch_norm__32, batch_norm__33, batch_norm__34, batch_norm__35 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_3, parameter_26, parameter_27, parameter_28, parameter_29, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x12x28x28xf32) <- (-1x12x28x28xf32)
        relu__3 = paddle._C_ops.relu_(batch_norm__30)

        # builtin.combine: ([-1x12x28x28xf32, -1x12x28x28xf32]) <- (-1x12x28x28xf32, -1x12x28x28xf32)
        combine_0 = [relu__1, relu__3]

        # pd_op.full: (1xi32) <- ()
        full_0 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.concat: (-1x24x28x28xf32) <- ([-1x12x28x28xf32, -1x12x28x28xf32], 1xi32)
        concat_0 = paddle._C_ops.concat(combine_0, full_0)

        # pd_op.shape: (4xi32) <- (-1x24x28x28xf32)
        shape_0 = paddle._C_ops.shape(concat_0)

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_1 = [0]

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_2 = [1]

        # pd_op.slice: (1xi32) <- (4xi32, 1xi64, 1xi64)
        slice_0 = paddle._C_ops.slice(shape_0, [0], full_int_array_1, full_int_array_2, [1], [0])

        # pd_op.full: (1xi32) <- ()
        full_1 = paddle._C_ops.full([1], float('2'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_2 = paddle._C_ops.full([1], float('12'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_3 = paddle._C_ops.full([1], float('28'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_4 = paddle._C_ops.full([1], float('28'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32, 1xi32)
        combine_1 = [slice_0, full_1, full_2, full_3, full_4]

        # pd_op.reshape_: (-1x2x12x28x28xf32, 0x-1x24x28x28xf32) <- (-1x24x28x28xf32, [1xi32, 1xi32, 1xi32, 1xi32, 1xi32])
        reshape__0, reshape__1 = (lambda x, f: f(x))(paddle._C_ops.reshape_(concat_0, [x.reshape([]) for x in combine_1]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.transpose: (-1x12x2x28x28xf32) <- (-1x2x12x28x28xf32)
        transpose_0 = paddle._C_ops.transpose(reshape__0, [0, 2, 1, 3, 4])

        # pd_op.full: (1xi32) <- ()
        full_5 = paddle._C_ops.full([1], float('24'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_6 = paddle._C_ops.full([1], float('28'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_7 = paddle._C_ops.full([1], float('28'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32)
        combine_2 = [slice_0, full_5, full_6, full_7]

        # pd_op.reshape_: (-1x24x28x28xf32, 0x-1x12x2x28x28xf32) <- (-1x12x2x28x28xf32, [1xi32, 1xi32, 1xi32, 1xi32])
        reshape__2, reshape__3 = (lambda x, f: f(x))(paddle._C_ops.reshape_(transpose_0, [x.reshape([]) for x in combine_2]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_3 = [12, 12]

        # pd_op.full: (1xi32) <- ()
        full_8 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.split: ([-1x12x28x28xf32, -1x12x28x28xf32]) <- (-1x24x28x28xf32, 2xi64, 1xi32)
        split_0 = paddle._C_ops.split(reshape__2, full_int_array_3, full_8)

        # builtin.slice: (-1x12x28x28xf32) <- ([-1x12x28x28xf32, -1x12x28x28xf32])
        slice_1 = split_0[1]

        # pd_op.conv2d: (-1x12x28x28xf32) <- (-1x12x28x28xf32, 12x12x1x1xf32)
        conv2d_4 = paddle._C_ops.conv2d(slice_1, parameter_30, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32, None) <- (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32)
        batch_norm__36, batch_norm__37, batch_norm__38, batch_norm__39, batch_norm__40, batch_norm__41 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_4, parameter_31, parameter_32, parameter_33, parameter_34, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x12x28x28xf32) <- (-1x12x28x28xf32)
        relu__4 = paddle._C_ops.relu_(batch_norm__36)

        # pd_op.depthwise_conv2d: (-1x12x28x28xf32) <- (-1x12x28x28xf32, 12x1x3x3xf32)
        depthwise_conv2d_2 = paddle._C_ops.depthwise_conv2d(relu__4, parameter_35, [1, 1], [1, 1], 'EXPLICIT', 12, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32, None) <- (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32)
        batch_norm__42, batch_norm__43, batch_norm__44, batch_norm__45, batch_norm__46, batch_norm__47 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_2, parameter_36, parameter_37, parameter_38, parameter_39, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x12x28x28xf32) <- (-1x12x28x28xf32, 12x12x1x1xf32)
        conv2d_5 = paddle._C_ops.conv2d(batch_norm__42, parameter_40, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32, None) <- (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32)
        batch_norm__48, batch_norm__49, batch_norm__50, batch_norm__51, batch_norm__52, batch_norm__53 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_5, parameter_41, parameter_42, parameter_43, parameter_44, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x12x28x28xf32) <- (-1x12x28x28xf32)
        relu__5 = paddle._C_ops.relu_(batch_norm__48)

        # builtin.slice: (-1x12x28x28xf32) <- ([-1x12x28x28xf32, -1x12x28x28xf32])
        slice_2 = split_0[0]

        # builtin.combine: ([-1x12x28x28xf32, -1x12x28x28xf32]) <- (-1x12x28x28xf32, -1x12x28x28xf32)
        combine_3 = [slice_2, relu__5]

        # pd_op.full: (1xi32) <- ()
        full_9 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.concat: (-1x24x28x28xf32) <- ([-1x12x28x28xf32, -1x12x28x28xf32], 1xi32)
        concat_1 = paddle._C_ops.concat(combine_3, full_9)

        # pd_op.shape: (4xi32) <- (-1x24x28x28xf32)
        shape_1 = paddle._C_ops.shape(concat_1)

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_4 = [0]

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_5 = [1]

        # pd_op.slice: (1xi32) <- (4xi32, 1xi64, 1xi64)
        slice_3 = paddle._C_ops.slice(shape_1, [0], full_int_array_4, full_int_array_5, [1], [0])

        # pd_op.full: (1xi32) <- ()
        full_10 = paddle._C_ops.full([1], float('2'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_11 = paddle._C_ops.full([1], float('12'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_12 = paddle._C_ops.full([1], float('28'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_13 = paddle._C_ops.full([1], float('28'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32, 1xi32)
        combine_4 = [slice_3, full_10, full_11, full_12, full_13]

        # pd_op.reshape_: (-1x2x12x28x28xf32, 0x-1x24x28x28xf32) <- (-1x24x28x28xf32, [1xi32, 1xi32, 1xi32, 1xi32, 1xi32])
        reshape__4, reshape__5 = (lambda x, f: f(x))(paddle._C_ops.reshape_(concat_1, [x.reshape([]) for x in combine_4]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.transpose: (-1x12x2x28x28xf32) <- (-1x2x12x28x28xf32)
        transpose_1 = paddle._C_ops.transpose(reshape__4, [0, 2, 1, 3, 4])

        # pd_op.full: (1xi32) <- ()
        full_14 = paddle._C_ops.full([1], float('24'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_15 = paddle._C_ops.full([1], float('28'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_16 = paddle._C_ops.full([1], float('28'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32)
        combine_5 = [slice_3, full_14, full_15, full_16]

        # pd_op.reshape_: (-1x24x28x28xf32, 0x-1x12x2x28x28xf32) <- (-1x12x2x28x28xf32, [1xi32, 1xi32, 1xi32, 1xi32])
        reshape__6, reshape__7 = (lambda x, f: f(x))(paddle._C_ops.reshape_(transpose_1, [x.reshape([]) for x in combine_5]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_6 = [12, 12]

        # pd_op.full: (1xi32) <- ()
        full_17 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.split: ([-1x12x28x28xf32, -1x12x28x28xf32]) <- (-1x24x28x28xf32, 2xi64, 1xi32)
        split_1 = paddle._C_ops.split(reshape__6, full_int_array_6, full_17)

        # builtin.slice: (-1x12x28x28xf32) <- ([-1x12x28x28xf32, -1x12x28x28xf32])
        slice_4 = split_1[1]

        # pd_op.conv2d: (-1x12x28x28xf32) <- (-1x12x28x28xf32, 12x12x1x1xf32)
        conv2d_6 = paddle._C_ops.conv2d(slice_4, parameter_45, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32, None) <- (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32)
        batch_norm__54, batch_norm__55, batch_norm__56, batch_norm__57, batch_norm__58, batch_norm__59 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_6, parameter_46, parameter_47, parameter_48, parameter_49, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x12x28x28xf32) <- (-1x12x28x28xf32)
        relu__6 = paddle._C_ops.relu_(batch_norm__54)

        # pd_op.depthwise_conv2d: (-1x12x28x28xf32) <- (-1x12x28x28xf32, 12x1x3x3xf32)
        depthwise_conv2d_3 = paddle._C_ops.depthwise_conv2d(relu__6, parameter_50, [1, 1], [1, 1], 'EXPLICIT', 12, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32, None) <- (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32)
        batch_norm__60, batch_norm__61, batch_norm__62, batch_norm__63, batch_norm__64, batch_norm__65 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_3, parameter_51, parameter_52, parameter_53, parameter_54, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x12x28x28xf32) <- (-1x12x28x28xf32, 12x12x1x1xf32)
        conv2d_7 = paddle._C_ops.conv2d(batch_norm__60, parameter_55, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32, None) <- (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32)
        batch_norm__66, batch_norm__67, batch_norm__68, batch_norm__69, batch_norm__70, batch_norm__71 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_7, parameter_56, parameter_57, parameter_58, parameter_59, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x12x28x28xf32) <- (-1x12x28x28xf32)
        relu__7 = paddle._C_ops.relu_(batch_norm__66)

        # builtin.slice: (-1x12x28x28xf32) <- ([-1x12x28x28xf32, -1x12x28x28xf32])
        slice_5 = split_1[0]

        # builtin.combine: ([-1x12x28x28xf32, -1x12x28x28xf32]) <- (-1x12x28x28xf32, -1x12x28x28xf32)
        combine_6 = [slice_5, relu__7]

        # pd_op.full: (1xi32) <- ()
        full_18 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.concat: (-1x24x28x28xf32) <- ([-1x12x28x28xf32, -1x12x28x28xf32], 1xi32)
        concat_2 = paddle._C_ops.concat(combine_6, full_18)

        # pd_op.shape: (4xi32) <- (-1x24x28x28xf32)
        shape_2 = paddle._C_ops.shape(concat_2)

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_7 = [0]

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_8 = [1]

        # pd_op.slice: (1xi32) <- (4xi32, 1xi64, 1xi64)
        slice_6 = paddle._C_ops.slice(shape_2, [0], full_int_array_7, full_int_array_8, [1], [0])

        # pd_op.full: (1xi32) <- ()
        full_19 = paddle._C_ops.full([1], float('2'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_20 = paddle._C_ops.full([1], float('12'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_21 = paddle._C_ops.full([1], float('28'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_22 = paddle._C_ops.full([1], float('28'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32, 1xi32)
        combine_7 = [slice_6, full_19, full_20, full_21, full_22]

        # pd_op.reshape_: (-1x2x12x28x28xf32, 0x-1x24x28x28xf32) <- (-1x24x28x28xf32, [1xi32, 1xi32, 1xi32, 1xi32, 1xi32])
        reshape__8, reshape__9 = (lambda x, f: f(x))(paddle._C_ops.reshape_(concat_2, [x.reshape([]) for x in combine_7]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.transpose: (-1x12x2x28x28xf32) <- (-1x2x12x28x28xf32)
        transpose_2 = paddle._C_ops.transpose(reshape__8, [0, 2, 1, 3, 4])

        # pd_op.full: (1xi32) <- ()
        full_23 = paddle._C_ops.full([1], float('24'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_24 = paddle._C_ops.full([1], float('28'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_25 = paddle._C_ops.full([1], float('28'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32)
        combine_8 = [slice_6, full_23, full_24, full_25]

        # pd_op.reshape_: (-1x24x28x28xf32, 0x-1x12x2x28x28xf32) <- (-1x12x2x28x28xf32, [1xi32, 1xi32, 1xi32, 1xi32])
        reshape__10, reshape__11 = (lambda x, f: f(x))(paddle._C_ops.reshape_(transpose_2, [x.reshape([]) for x in combine_8]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_9 = [12, 12]

        # pd_op.full: (1xi32) <- ()
        full_26 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.split: ([-1x12x28x28xf32, -1x12x28x28xf32]) <- (-1x24x28x28xf32, 2xi64, 1xi32)
        split_2 = paddle._C_ops.split(reshape__10, full_int_array_9, full_26)

        # builtin.slice: (-1x12x28x28xf32) <- ([-1x12x28x28xf32, -1x12x28x28xf32])
        slice_7 = split_2[1]

        # pd_op.conv2d: (-1x12x28x28xf32) <- (-1x12x28x28xf32, 12x12x1x1xf32)
        conv2d_8 = paddle._C_ops.conv2d(slice_7, parameter_60, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32, None) <- (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32)
        batch_norm__72, batch_norm__73, batch_norm__74, batch_norm__75, batch_norm__76, batch_norm__77 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_8, parameter_61, parameter_62, parameter_63, parameter_64, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x12x28x28xf32) <- (-1x12x28x28xf32)
        relu__8 = paddle._C_ops.relu_(batch_norm__72)

        # pd_op.depthwise_conv2d: (-1x12x28x28xf32) <- (-1x12x28x28xf32, 12x1x3x3xf32)
        depthwise_conv2d_4 = paddle._C_ops.depthwise_conv2d(relu__8, parameter_65, [1, 1], [1, 1], 'EXPLICIT', 12, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32, None) <- (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32)
        batch_norm__78, batch_norm__79, batch_norm__80, batch_norm__81, batch_norm__82, batch_norm__83 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_4, parameter_66, parameter_67, parameter_68, parameter_69, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x12x28x28xf32) <- (-1x12x28x28xf32, 12x12x1x1xf32)
        conv2d_9 = paddle._C_ops.conv2d(batch_norm__78, parameter_70, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32, None) <- (-1x12x28x28xf32, 12xf32, 12xf32, 12xf32, 12xf32)
        batch_norm__84, batch_norm__85, batch_norm__86, batch_norm__87, batch_norm__88, batch_norm__89 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_9, parameter_71, parameter_72, parameter_73, parameter_74, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x12x28x28xf32) <- (-1x12x28x28xf32)
        relu__9 = paddle._C_ops.relu_(batch_norm__84)

        # builtin.slice: (-1x12x28x28xf32) <- ([-1x12x28x28xf32, -1x12x28x28xf32])
        slice_8 = split_2[0]

        # builtin.combine: ([-1x12x28x28xf32, -1x12x28x28xf32]) <- (-1x12x28x28xf32, -1x12x28x28xf32)
        combine_9 = [slice_8, relu__9]

        # pd_op.full: (1xi32) <- ()
        full_27 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.concat: (-1x24x28x28xf32) <- ([-1x12x28x28xf32, -1x12x28x28xf32], 1xi32)
        concat_3 = paddle._C_ops.concat(combine_9, full_27)

        # pd_op.shape: (4xi32) <- (-1x24x28x28xf32)
        shape_3 = paddle._C_ops.shape(concat_3)

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_10 = [0]

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_11 = [1]

        # pd_op.slice: (1xi32) <- (4xi32, 1xi64, 1xi64)
        slice_9 = paddle._C_ops.slice(shape_3, [0], full_int_array_10, full_int_array_11, [1], [0])

        # pd_op.full: (1xi32) <- ()
        full_28 = paddle._C_ops.full([1], float('2'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_29 = paddle._C_ops.full([1], float('12'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_30 = paddle._C_ops.full([1], float('28'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_31 = paddle._C_ops.full([1], float('28'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32, 1xi32)
        combine_10 = [slice_9, full_28, full_29, full_30, full_31]

        # pd_op.reshape_: (-1x2x12x28x28xf32, 0x-1x24x28x28xf32) <- (-1x24x28x28xf32, [1xi32, 1xi32, 1xi32, 1xi32, 1xi32])
        reshape__12, reshape__13 = (lambda x, f: f(x))(paddle._C_ops.reshape_(concat_3, [x.reshape([]) for x in combine_10]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.transpose: (-1x12x2x28x28xf32) <- (-1x2x12x28x28xf32)
        transpose_3 = paddle._C_ops.transpose(reshape__12, [0, 2, 1, 3, 4])

        # pd_op.full: (1xi32) <- ()
        full_32 = paddle._C_ops.full([1], float('24'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_33 = paddle._C_ops.full([1], float('28'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_34 = paddle._C_ops.full([1], float('28'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32)
        combine_11 = [slice_9, full_32, full_33, full_34]

        # pd_op.reshape_: (-1x24x28x28xf32, 0x-1x12x2x28x28xf32) <- (-1x12x2x28x28xf32, [1xi32, 1xi32, 1xi32, 1xi32])
        reshape__14, reshape__15 = (lambda x, f: f(x))(paddle._C_ops.reshape_(transpose_3, [x.reshape([]) for x in combine_11]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.depthwise_conv2d: (-1x24x14x14xf32) <- (-1x24x28x28xf32, 24x1x3x3xf32)
        depthwise_conv2d_5 = paddle._C_ops.depthwise_conv2d(reshape__14, parameter_75, [2, 2], [1, 1], 'EXPLICIT', 24, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__90, batch_norm__91, batch_norm__92, batch_norm__93, batch_norm__94, batch_norm__95 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_5, parameter_76, parameter_77, parameter_78, parameter_79, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x24x1x1xf32)
        conv2d_10 = paddle._C_ops.conv2d(batch_norm__90, parameter_80, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__96, batch_norm__97, batch_norm__98, batch_norm__99, batch_norm__100, batch_norm__101 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_10, parameter_81, parameter_82, parameter_83, parameter_84, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x14x14xf32) <- (-1x24x14x14xf32)
        relu__10 = paddle._C_ops.relu_(batch_norm__96)

        # pd_op.conv2d: (-1x24x28x28xf32) <- (-1x24x28x28xf32, 24x24x1x1xf32)
        conv2d_11 = paddle._C_ops.conv2d(reshape__14, parameter_85, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x28x28xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x28x28xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__102, batch_norm__103, batch_norm__104, batch_norm__105, batch_norm__106, batch_norm__107 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_11, parameter_86, parameter_87, parameter_88, parameter_89, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x28x28xf32) <- (-1x24x28x28xf32)
        relu__11 = paddle._C_ops.relu_(batch_norm__102)

        # pd_op.depthwise_conv2d: (-1x24x14x14xf32) <- (-1x24x28x28xf32, 24x1x3x3xf32)
        depthwise_conv2d_6 = paddle._C_ops.depthwise_conv2d(relu__11, parameter_90, [2, 2], [1, 1], 'EXPLICIT', 24, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__108, batch_norm__109, batch_norm__110, batch_norm__111, batch_norm__112, batch_norm__113 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_6, parameter_91, parameter_92, parameter_93, parameter_94, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x24x1x1xf32)
        conv2d_12 = paddle._C_ops.conv2d(batch_norm__108, parameter_95, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__114, batch_norm__115, batch_norm__116, batch_norm__117, batch_norm__118, batch_norm__119 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_12, parameter_96, parameter_97, parameter_98, parameter_99, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x14x14xf32) <- (-1x24x14x14xf32)
        relu__12 = paddle._C_ops.relu_(batch_norm__114)

        # builtin.combine: ([-1x24x14x14xf32, -1x24x14x14xf32]) <- (-1x24x14x14xf32, -1x24x14x14xf32)
        combine_12 = [relu__10, relu__12]

        # pd_op.full: (1xi32) <- ()
        full_35 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.concat: (-1x48x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32], 1xi32)
        concat_4 = paddle._C_ops.concat(combine_12, full_35)

        # pd_op.shape: (4xi32) <- (-1x48x14x14xf32)
        shape_4 = paddle._C_ops.shape(concat_4)

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_12 = [0]

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_13 = [1]

        # pd_op.slice: (1xi32) <- (4xi32, 1xi64, 1xi64)
        slice_10 = paddle._C_ops.slice(shape_4, [0], full_int_array_12, full_int_array_13, [1], [0])

        # pd_op.full: (1xi32) <- ()
        full_36 = paddle._C_ops.full([1], float('2'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_37 = paddle._C_ops.full([1], float('24'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_38 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_39 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32, 1xi32)
        combine_13 = [slice_10, full_36, full_37, full_38, full_39]

        # pd_op.reshape_: (-1x2x24x14x14xf32, 0x-1x48x14x14xf32) <- (-1x48x14x14xf32, [1xi32, 1xi32, 1xi32, 1xi32, 1xi32])
        reshape__16, reshape__17 = (lambda x, f: f(x))(paddle._C_ops.reshape_(concat_4, [x.reshape([]) for x in combine_13]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.transpose: (-1x24x2x14x14xf32) <- (-1x2x24x14x14xf32)
        transpose_4 = paddle._C_ops.transpose(reshape__16, [0, 2, 1, 3, 4])

        # pd_op.full: (1xi32) <- ()
        full_40 = paddle._C_ops.full([1], float('48'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_41 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_42 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32)
        combine_14 = [slice_10, full_40, full_41, full_42]

        # pd_op.reshape_: (-1x48x14x14xf32, 0x-1x24x2x14x14xf32) <- (-1x24x2x14x14xf32, [1xi32, 1xi32, 1xi32, 1xi32])
        reshape__18, reshape__19 = (lambda x, f: f(x))(paddle._C_ops.reshape_(transpose_4, [x.reshape([]) for x in combine_14]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_14 = [24, 24]

        # pd_op.full: (1xi32) <- ()
        full_43 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.split: ([-1x24x14x14xf32, -1x24x14x14xf32]) <- (-1x48x14x14xf32, 2xi64, 1xi32)
        split_3 = paddle._C_ops.split(reshape__18, full_int_array_14, full_43)

        # builtin.slice: (-1x24x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32])
        slice_11 = split_3[1]

        # pd_op.conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x24x1x1xf32)
        conv2d_13 = paddle._C_ops.conv2d(slice_11, parameter_100, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__120, batch_norm__121, batch_norm__122, batch_norm__123, batch_norm__124, batch_norm__125 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_13, parameter_101, parameter_102, parameter_103, parameter_104, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x14x14xf32) <- (-1x24x14x14xf32)
        relu__13 = paddle._C_ops.relu_(batch_norm__120)

        # pd_op.depthwise_conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x1x3x3xf32)
        depthwise_conv2d_7 = paddle._C_ops.depthwise_conv2d(relu__13, parameter_105, [1, 1], [1, 1], 'EXPLICIT', 24, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__126, batch_norm__127, batch_norm__128, batch_norm__129, batch_norm__130, batch_norm__131 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_7, parameter_106, parameter_107, parameter_108, parameter_109, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x24x1x1xf32)
        conv2d_14 = paddle._C_ops.conv2d(batch_norm__126, parameter_110, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__132, batch_norm__133, batch_norm__134, batch_norm__135, batch_norm__136, batch_norm__137 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_14, parameter_111, parameter_112, parameter_113, parameter_114, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x14x14xf32) <- (-1x24x14x14xf32)
        relu__14 = paddle._C_ops.relu_(batch_norm__132)

        # builtin.slice: (-1x24x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32])
        slice_12 = split_3[0]

        # builtin.combine: ([-1x24x14x14xf32, -1x24x14x14xf32]) <- (-1x24x14x14xf32, -1x24x14x14xf32)
        combine_15 = [slice_12, relu__14]

        # pd_op.full: (1xi32) <- ()
        full_44 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.concat: (-1x48x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32], 1xi32)
        concat_5 = paddle._C_ops.concat(combine_15, full_44)

        # pd_op.shape: (4xi32) <- (-1x48x14x14xf32)
        shape_5 = paddle._C_ops.shape(concat_5)

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_15 = [0]

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_16 = [1]

        # pd_op.slice: (1xi32) <- (4xi32, 1xi64, 1xi64)
        slice_13 = paddle._C_ops.slice(shape_5, [0], full_int_array_15, full_int_array_16, [1], [0])

        # pd_op.full: (1xi32) <- ()
        full_45 = paddle._C_ops.full([1], float('2'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_46 = paddle._C_ops.full([1], float('24'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_47 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_48 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32, 1xi32)
        combine_16 = [slice_13, full_45, full_46, full_47, full_48]

        # pd_op.reshape_: (-1x2x24x14x14xf32, 0x-1x48x14x14xf32) <- (-1x48x14x14xf32, [1xi32, 1xi32, 1xi32, 1xi32, 1xi32])
        reshape__20, reshape__21 = (lambda x, f: f(x))(paddle._C_ops.reshape_(concat_5, [x.reshape([]) for x in combine_16]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.transpose: (-1x24x2x14x14xf32) <- (-1x2x24x14x14xf32)
        transpose_5 = paddle._C_ops.transpose(reshape__20, [0, 2, 1, 3, 4])

        # pd_op.full: (1xi32) <- ()
        full_49 = paddle._C_ops.full([1], float('48'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_50 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_51 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32)
        combine_17 = [slice_13, full_49, full_50, full_51]

        # pd_op.reshape_: (-1x48x14x14xf32, 0x-1x24x2x14x14xf32) <- (-1x24x2x14x14xf32, [1xi32, 1xi32, 1xi32, 1xi32])
        reshape__22, reshape__23 = (lambda x, f: f(x))(paddle._C_ops.reshape_(transpose_5, [x.reshape([]) for x in combine_17]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_17 = [24, 24]

        # pd_op.full: (1xi32) <- ()
        full_52 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.split: ([-1x24x14x14xf32, -1x24x14x14xf32]) <- (-1x48x14x14xf32, 2xi64, 1xi32)
        split_4 = paddle._C_ops.split(reshape__22, full_int_array_17, full_52)

        # builtin.slice: (-1x24x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32])
        slice_14 = split_4[1]

        # pd_op.conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x24x1x1xf32)
        conv2d_15 = paddle._C_ops.conv2d(slice_14, parameter_115, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__138, batch_norm__139, batch_norm__140, batch_norm__141, batch_norm__142, batch_norm__143 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_15, parameter_116, parameter_117, parameter_118, parameter_119, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x14x14xf32) <- (-1x24x14x14xf32)
        relu__15 = paddle._C_ops.relu_(batch_norm__138)

        # pd_op.depthwise_conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x1x3x3xf32)
        depthwise_conv2d_8 = paddle._C_ops.depthwise_conv2d(relu__15, parameter_120, [1, 1], [1, 1], 'EXPLICIT', 24, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__144, batch_norm__145, batch_norm__146, batch_norm__147, batch_norm__148, batch_norm__149 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_8, parameter_121, parameter_122, parameter_123, parameter_124, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x24x1x1xf32)
        conv2d_16 = paddle._C_ops.conv2d(batch_norm__144, parameter_125, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__150, batch_norm__151, batch_norm__152, batch_norm__153, batch_norm__154, batch_norm__155 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_16, parameter_126, parameter_127, parameter_128, parameter_129, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x14x14xf32) <- (-1x24x14x14xf32)
        relu__16 = paddle._C_ops.relu_(batch_norm__150)

        # builtin.slice: (-1x24x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32])
        slice_15 = split_4[0]

        # builtin.combine: ([-1x24x14x14xf32, -1x24x14x14xf32]) <- (-1x24x14x14xf32, -1x24x14x14xf32)
        combine_18 = [slice_15, relu__16]

        # pd_op.full: (1xi32) <- ()
        full_53 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.concat: (-1x48x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32], 1xi32)
        concat_6 = paddle._C_ops.concat(combine_18, full_53)

        # pd_op.shape: (4xi32) <- (-1x48x14x14xf32)
        shape_6 = paddle._C_ops.shape(concat_6)

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_18 = [0]

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_19 = [1]

        # pd_op.slice: (1xi32) <- (4xi32, 1xi64, 1xi64)
        slice_16 = paddle._C_ops.slice(shape_6, [0], full_int_array_18, full_int_array_19, [1], [0])

        # pd_op.full: (1xi32) <- ()
        full_54 = paddle._C_ops.full([1], float('2'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_55 = paddle._C_ops.full([1], float('24'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_56 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_57 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32, 1xi32)
        combine_19 = [slice_16, full_54, full_55, full_56, full_57]

        # pd_op.reshape_: (-1x2x24x14x14xf32, 0x-1x48x14x14xf32) <- (-1x48x14x14xf32, [1xi32, 1xi32, 1xi32, 1xi32, 1xi32])
        reshape__24, reshape__25 = (lambda x, f: f(x))(paddle._C_ops.reshape_(concat_6, [x.reshape([]) for x in combine_19]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.transpose: (-1x24x2x14x14xf32) <- (-1x2x24x14x14xf32)
        transpose_6 = paddle._C_ops.transpose(reshape__24, [0, 2, 1, 3, 4])

        # pd_op.full: (1xi32) <- ()
        full_58 = paddle._C_ops.full([1], float('48'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_59 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_60 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32)
        combine_20 = [slice_16, full_58, full_59, full_60]

        # pd_op.reshape_: (-1x48x14x14xf32, 0x-1x24x2x14x14xf32) <- (-1x24x2x14x14xf32, [1xi32, 1xi32, 1xi32, 1xi32])
        reshape__26, reshape__27 = (lambda x, f: f(x))(paddle._C_ops.reshape_(transpose_6, [x.reshape([]) for x in combine_20]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_20 = [24, 24]

        # pd_op.full: (1xi32) <- ()
        full_61 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.split: ([-1x24x14x14xf32, -1x24x14x14xf32]) <- (-1x48x14x14xf32, 2xi64, 1xi32)
        split_5 = paddle._C_ops.split(reshape__26, full_int_array_20, full_61)

        # builtin.slice: (-1x24x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32])
        slice_17 = split_5[1]

        # pd_op.conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x24x1x1xf32)
        conv2d_17 = paddle._C_ops.conv2d(slice_17, parameter_130, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__156, batch_norm__157, batch_norm__158, batch_norm__159, batch_norm__160, batch_norm__161 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_17, parameter_131, parameter_132, parameter_133, parameter_134, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x14x14xf32) <- (-1x24x14x14xf32)
        relu__17 = paddle._C_ops.relu_(batch_norm__156)

        # pd_op.depthwise_conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x1x3x3xf32)
        depthwise_conv2d_9 = paddle._C_ops.depthwise_conv2d(relu__17, parameter_135, [1, 1], [1, 1], 'EXPLICIT', 24, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__162, batch_norm__163, batch_norm__164, batch_norm__165, batch_norm__166, batch_norm__167 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_9, parameter_136, parameter_137, parameter_138, parameter_139, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x24x1x1xf32)
        conv2d_18 = paddle._C_ops.conv2d(batch_norm__162, parameter_140, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__168, batch_norm__169, batch_norm__170, batch_norm__171, batch_norm__172, batch_norm__173 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_18, parameter_141, parameter_142, parameter_143, parameter_144, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x14x14xf32) <- (-1x24x14x14xf32)
        relu__18 = paddle._C_ops.relu_(batch_norm__168)

        # builtin.slice: (-1x24x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32])
        slice_18 = split_5[0]

        # builtin.combine: ([-1x24x14x14xf32, -1x24x14x14xf32]) <- (-1x24x14x14xf32, -1x24x14x14xf32)
        combine_21 = [slice_18, relu__18]

        # pd_op.full: (1xi32) <- ()
        full_62 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.concat: (-1x48x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32], 1xi32)
        concat_7 = paddle._C_ops.concat(combine_21, full_62)

        # pd_op.shape: (4xi32) <- (-1x48x14x14xf32)
        shape_7 = paddle._C_ops.shape(concat_7)

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_21 = [0]

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_22 = [1]

        # pd_op.slice: (1xi32) <- (4xi32, 1xi64, 1xi64)
        slice_19 = paddle._C_ops.slice(shape_7, [0], full_int_array_21, full_int_array_22, [1], [0])

        # pd_op.full: (1xi32) <- ()
        full_63 = paddle._C_ops.full([1], float('2'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_64 = paddle._C_ops.full([1], float('24'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_65 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_66 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32, 1xi32)
        combine_22 = [slice_19, full_63, full_64, full_65, full_66]

        # pd_op.reshape_: (-1x2x24x14x14xf32, 0x-1x48x14x14xf32) <- (-1x48x14x14xf32, [1xi32, 1xi32, 1xi32, 1xi32, 1xi32])
        reshape__28, reshape__29 = (lambda x, f: f(x))(paddle._C_ops.reshape_(concat_7, [x.reshape([]) for x in combine_22]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.transpose: (-1x24x2x14x14xf32) <- (-1x2x24x14x14xf32)
        transpose_7 = paddle._C_ops.transpose(reshape__28, [0, 2, 1, 3, 4])

        # pd_op.full: (1xi32) <- ()
        full_67 = paddle._C_ops.full([1], float('48'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_68 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_69 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32)
        combine_23 = [slice_19, full_67, full_68, full_69]

        # pd_op.reshape_: (-1x48x14x14xf32, 0x-1x24x2x14x14xf32) <- (-1x24x2x14x14xf32, [1xi32, 1xi32, 1xi32, 1xi32])
        reshape__30, reshape__31 = (lambda x, f: f(x))(paddle._C_ops.reshape_(transpose_7, [x.reshape([]) for x in combine_23]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_23 = [24, 24]

        # pd_op.full: (1xi32) <- ()
        full_70 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.split: ([-1x24x14x14xf32, -1x24x14x14xf32]) <- (-1x48x14x14xf32, 2xi64, 1xi32)
        split_6 = paddle._C_ops.split(reshape__30, full_int_array_23, full_70)

        # builtin.slice: (-1x24x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32])
        slice_20 = split_6[1]

        # pd_op.conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x24x1x1xf32)
        conv2d_19 = paddle._C_ops.conv2d(slice_20, parameter_145, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__174, batch_norm__175, batch_norm__176, batch_norm__177, batch_norm__178, batch_norm__179 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_19, parameter_146, parameter_147, parameter_148, parameter_149, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x14x14xf32) <- (-1x24x14x14xf32)
        relu__19 = paddle._C_ops.relu_(batch_norm__174)

        # pd_op.depthwise_conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x1x3x3xf32)
        depthwise_conv2d_10 = paddle._C_ops.depthwise_conv2d(relu__19, parameter_150, [1, 1], [1, 1], 'EXPLICIT', 24, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__180, batch_norm__181, batch_norm__182, batch_norm__183, batch_norm__184, batch_norm__185 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_10, parameter_151, parameter_152, parameter_153, parameter_154, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x24x1x1xf32)
        conv2d_20 = paddle._C_ops.conv2d(batch_norm__180, parameter_155, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__186, batch_norm__187, batch_norm__188, batch_norm__189, batch_norm__190, batch_norm__191 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_20, parameter_156, parameter_157, parameter_158, parameter_159, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x14x14xf32) <- (-1x24x14x14xf32)
        relu__20 = paddle._C_ops.relu_(batch_norm__186)

        # builtin.slice: (-1x24x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32])
        slice_21 = split_6[0]

        # builtin.combine: ([-1x24x14x14xf32, -1x24x14x14xf32]) <- (-1x24x14x14xf32, -1x24x14x14xf32)
        combine_24 = [slice_21, relu__20]

        # pd_op.full: (1xi32) <- ()
        full_71 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.concat: (-1x48x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32], 1xi32)
        concat_8 = paddle._C_ops.concat(combine_24, full_71)

        # pd_op.shape: (4xi32) <- (-1x48x14x14xf32)
        shape_8 = paddle._C_ops.shape(concat_8)

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_24 = [0]

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_25 = [1]

        # pd_op.slice: (1xi32) <- (4xi32, 1xi64, 1xi64)
        slice_22 = paddle._C_ops.slice(shape_8, [0], full_int_array_24, full_int_array_25, [1], [0])

        # pd_op.full: (1xi32) <- ()
        full_72 = paddle._C_ops.full([1], float('2'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_73 = paddle._C_ops.full([1], float('24'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_74 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_75 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32, 1xi32)
        combine_25 = [slice_22, full_72, full_73, full_74, full_75]

        # pd_op.reshape_: (-1x2x24x14x14xf32, 0x-1x48x14x14xf32) <- (-1x48x14x14xf32, [1xi32, 1xi32, 1xi32, 1xi32, 1xi32])
        reshape__32, reshape__33 = (lambda x, f: f(x))(paddle._C_ops.reshape_(concat_8, [x.reshape([]) for x in combine_25]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.transpose: (-1x24x2x14x14xf32) <- (-1x2x24x14x14xf32)
        transpose_8 = paddle._C_ops.transpose(reshape__32, [0, 2, 1, 3, 4])

        # pd_op.full: (1xi32) <- ()
        full_76 = paddle._C_ops.full([1], float('48'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_77 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_78 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32)
        combine_26 = [slice_22, full_76, full_77, full_78]

        # pd_op.reshape_: (-1x48x14x14xf32, 0x-1x24x2x14x14xf32) <- (-1x24x2x14x14xf32, [1xi32, 1xi32, 1xi32, 1xi32])
        reshape__34, reshape__35 = (lambda x, f: f(x))(paddle._C_ops.reshape_(transpose_8, [x.reshape([]) for x in combine_26]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_26 = [24, 24]

        # pd_op.full: (1xi32) <- ()
        full_79 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.split: ([-1x24x14x14xf32, -1x24x14x14xf32]) <- (-1x48x14x14xf32, 2xi64, 1xi32)
        split_7 = paddle._C_ops.split(reshape__34, full_int_array_26, full_79)

        # builtin.slice: (-1x24x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32])
        slice_23 = split_7[1]

        # pd_op.conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x24x1x1xf32)
        conv2d_21 = paddle._C_ops.conv2d(slice_23, parameter_160, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__192, batch_norm__193, batch_norm__194, batch_norm__195, batch_norm__196, batch_norm__197 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_21, parameter_161, parameter_162, parameter_163, parameter_164, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x14x14xf32) <- (-1x24x14x14xf32)
        relu__21 = paddle._C_ops.relu_(batch_norm__192)

        # pd_op.depthwise_conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x1x3x3xf32)
        depthwise_conv2d_11 = paddle._C_ops.depthwise_conv2d(relu__21, parameter_165, [1, 1], [1, 1], 'EXPLICIT', 24, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__198, batch_norm__199, batch_norm__200, batch_norm__201, batch_norm__202, batch_norm__203 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_11, parameter_166, parameter_167, parameter_168, parameter_169, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x24x1x1xf32)
        conv2d_22 = paddle._C_ops.conv2d(batch_norm__198, parameter_170, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__204, batch_norm__205, batch_norm__206, batch_norm__207, batch_norm__208, batch_norm__209 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_22, parameter_171, parameter_172, parameter_173, parameter_174, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x14x14xf32) <- (-1x24x14x14xf32)
        relu__22 = paddle._C_ops.relu_(batch_norm__204)

        # builtin.slice: (-1x24x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32])
        slice_24 = split_7[0]

        # builtin.combine: ([-1x24x14x14xf32, -1x24x14x14xf32]) <- (-1x24x14x14xf32, -1x24x14x14xf32)
        combine_27 = [slice_24, relu__22]

        # pd_op.full: (1xi32) <- ()
        full_80 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.concat: (-1x48x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32], 1xi32)
        concat_9 = paddle._C_ops.concat(combine_27, full_80)

        # pd_op.shape: (4xi32) <- (-1x48x14x14xf32)
        shape_9 = paddle._C_ops.shape(concat_9)

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_27 = [0]

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_28 = [1]

        # pd_op.slice: (1xi32) <- (4xi32, 1xi64, 1xi64)
        slice_25 = paddle._C_ops.slice(shape_9, [0], full_int_array_27, full_int_array_28, [1], [0])

        # pd_op.full: (1xi32) <- ()
        full_81 = paddle._C_ops.full([1], float('2'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_82 = paddle._C_ops.full([1], float('24'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_83 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_84 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32, 1xi32)
        combine_28 = [slice_25, full_81, full_82, full_83, full_84]

        # pd_op.reshape_: (-1x2x24x14x14xf32, 0x-1x48x14x14xf32) <- (-1x48x14x14xf32, [1xi32, 1xi32, 1xi32, 1xi32, 1xi32])
        reshape__36, reshape__37 = (lambda x, f: f(x))(paddle._C_ops.reshape_(concat_9, [x.reshape([]) for x in combine_28]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.transpose: (-1x24x2x14x14xf32) <- (-1x2x24x14x14xf32)
        transpose_9 = paddle._C_ops.transpose(reshape__36, [0, 2, 1, 3, 4])

        # pd_op.full: (1xi32) <- ()
        full_85 = paddle._C_ops.full([1], float('48'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_86 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_87 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32)
        combine_29 = [slice_25, full_85, full_86, full_87]

        # pd_op.reshape_: (-1x48x14x14xf32, 0x-1x24x2x14x14xf32) <- (-1x24x2x14x14xf32, [1xi32, 1xi32, 1xi32, 1xi32])
        reshape__38, reshape__39 = (lambda x, f: f(x))(paddle._C_ops.reshape_(transpose_9, [x.reshape([]) for x in combine_29]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_29 = [24, 24]

        # pd_op.full: (1xi32) <- ()
        full_88 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.split: ([-1x24x14x14xf32, -1x24x14x14xf32]) <- (-1x48x14x14xf32, 2xi64, 1xi32)
        split_8 = paddle._C_ops.split(reshape__38, full_int_array_29, full_88)

        # builtin.slice: (-1x24x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32])
        slice_26 = split_8[1]

        # pd_op.conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x24x1x1xf32)
        conv2d_23 = paddle._C_ops.conv2d(slice_26, parameter_175, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__210, batch_norm__211, batch_norm__212, batch_norm__213, batch_norm__214, batch_norm__215 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_23, parameter_176, parameter_177, parameter_178, parameter_179, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x14x14xf32) <- (-1x24x14x14xf32)
        relu__23 = paddle._C_ops.relu_(batch_norm__210)

        # pd_op.depthwise_conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x1x3x3xf32)
        depthwise_conv2d_12 = paddle._C_ops.depthwise_conv2d(relu__23, parameter_180, [1, 1], [1, 1], 'EXPLICIT', 24, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__216, batch_norm__217, batch_norm__218, batch_norm__219, batch_norm__220, batch_norm__221 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_12, parameter_181, parameter_182, parameter_183, parameter_184, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x24x1x1xf32)
        conv2d_24 = paddle._C_ops.conv2d(batch_norm__216, parameter_185, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__222, batch_norm__223, batch_norm__224, batch_norm__225, batch_norm__226, batch_norm__227 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_24, parameter_186, parameter_187, parameter_188, parameter_189, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x14x14xf32) <- (-1x24x14x14xf32)
        relu__24 = paddle._C_ops.relu_(batch_norm__222)

        # builtin.slice: (-1x24x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32])
        slice_27 = split_8[0]

        # builtin.combine: ([-1x24x14x14xf32, -1x24x14x14xf32]) <- (-1x24x14x14xf32, -1x24x14x14xf32)
        combine_30 = [slice_27, relu__24]

        # pd_op.full: (1xi32) <- ()
        full_89 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.concat: (-1x48x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32], 1xi32)
        concat_10 = paddle._C_ops.concat(combine_30, full_89)

        # pd_op.shape: (4xi32) <- (-1x48x14x14xf32)
        shape_10 = paddle._C_ops.shape(concat_10)

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_30 = [0]

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_31 = [1]

        # pd_op.slice: (1xi32) <- (4xi32, 1xi64, 1xi64)
        slice_28 = paddle._C_ops.slice(shape_10, [0], full_int_array_30, full_int_array_31, [1], [0])

        # pd_op.full: (1xi32) <- ()
        full_90 = paddle._C_ops.full([1], float('2'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_91 = paddle._C_ops.full([1], float('24'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_92 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_93 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32, 1xi32)
        combine_31 = [slice_28, full_90, full_91, full_92, full_93]

        # pd_op.reshape_: (-1x2x24x14x14xf32, 0x-1x48x14x14xf32) <- (-1x48x14x14xf32, [1xi32, 1xi32, 1xi32, 1xi32, 1xi32])
        reshape__40, reshape__41 = (lambda x, f: f(x))(paddle._C_ops.reshape_(concat_10, [x.reshape([]) for x in combine_31]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.transpose: (-1x24x2x14x14xf32) <- (-1x2x24x14x14xf32)
        transpose_10 = paddle._C_ops.transpose(reshape__40, [0, 2, 1, 3, 4])

        # pd_op.full: (1xi32) <- ()
        full_94 = paddle._C_ops.full([1], float('48'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_95 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_96 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32)
        combine_32 = [slice_28, full_94, full_95, full_96]

        # pd_op.reshape_: (-1x48x14x14xf32, 0x-1x24x2x14x14xf32) <- (-1x24x2x14x14xf32, [1xi32, 1xi32, 1xi32, 1xi32])
        reshape__42, reshape__43 = (lambda x, f: f(x))(paddle._C_ops.reshape_(transpose_10, [x.reshape([]) for x in combine_32]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_32 = [24, 24]

        # pd_op.full: (1xi32) <- ()
        full_97 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.split: ([-1x24x14x14xf32, -1x24x14x14xf32]) <- (-1x48x14x14xf32, 2xi64, 1xi32)
        split_9 = paddle._C_ops.split(reshape__42, full_int_array_32, full_97)

        # builtin.slice: (-1x24x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32])
        slice_29 = split_9[1]

        # pd_op.conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x24x1x1xf32)
        conv2d_25 = paddle._C_ops.conv2d(slice_29, parameter_190, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__228, batch_norm__229, batch_norm__230, batch_norm__231, batch_norm__232, batch_norm__233 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_25, parameter_191, parameter_192, parameter_193, parameter_194, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x14x14xf32) <- (-1x24x14x14xf32)
        relu__25 = paddle._C_ops.relu_(batch_norm__228)

        # pd_op.depthwise_conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x1x3x3xf32)
        depthwise_conv2d_13 = paddle._C_ops.depthwise_conv2d(relu__25, parameter_195, [1, 1], [1, 1], 'EXPLICIT', 24, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__234, batch_norm__235, batch_norm__236, batch_norm__237, batch_norm__238, batch_norm__239 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_13, parameter_196, parameter_197, parameter_198, parameter_199, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x24x14x14xf32) <- (-1x24x14x14xf32, 24x24x1x1xf32)
        conv2d_26 = paddle._C_ops.conv2d(batch_norm__234, parameter_200, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x14x14xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__240, batch_norm__241, batch_norm__242, batch_norm__243, batch_norm__244, batch_norm__245 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_26, parameter_201, parameter_202, parameter_203, parameter_204, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x14x14xf32) <- (-1x24x14x14xf32)
        relu__26 = paddle._C_ops.relu_(batch_norm__240)

        # builtin.slice: (-1x24x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32])
        slice_30 = split_9[0]

        # builtin.combine: ([-1x24x14x14xf32, -1x24x14x14xf32]) <- (-1x24x14x14xf32, -1x24x14x14xf32)
        combine_33 = [slice_30, relu__26]

        # pd_op.full: (1xi32) <- ()
        full_98 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.concat: (-1x48x14x14xf32) <- ([-1x24x14x14xf32, -1x24x14x14xf32], 1xi32)
        concat_11 = paddle._C_ops.concat(combine_33, full_98)

        # pd_op.shape: (4xi32) <- (-1x48x14x14xf32)
        shape_11 = paddle._C_ops.shape(concat_11)

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_33 = [0]

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_34 = [1]

        # pd_op.slice: (1xi32) <- (4xi32, 1xi64, 1xi64)
        slice_31 = paddle._C_ops.slice(shape_11, [0], full_int_array_33, full_int_array_34, [1], [0])

        # pd_op.full: (1xi32) <- ()
        full_99 = paddle._C_ops.full([1], float('2'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_100 = paddle._C_ops.full([1], float('24'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_101 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_102 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32, 1xi32)
        combine_34 = [slice_31, full_99, full_100, full_101, full_102]

        # pd_op.reshape_: (-1x2x24x14x14xf32, 0x-1x48x14x14xf32) <- (-1x48x14x14xf32, [1xi32, 1xi32, 1xi32, 1xi32, 1xi32])
        reshape__44, reshape__45 = (lambda x, f: f(x))(paddle._C_ops.reshape_(concat_11, [x.reshape([]) for x in combine_34]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.transpose: (-1x24x2x14x14xf32) <- (-1x2x24x14x14xf32)
        transpose_11 = paddle._C_ops.transpose(reshape__44, [0, 2, 1, 3, 4])

        # pd_op.full: (1xi32) <- ()
        full_103 = paddle._C_ops.full([1], float('48'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_104 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_105 = paddle._C_ops.full([1], float('14'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32)
        combine_35 = [slice_31, full_103, full_104, full_105]

        # pd_op.reshape_: (-1x48x14x14xf32, 0x-1x24x2x14x14xf32) <- (-1x24x2x14x14xf32, [1xi32, 1xi32, 1xi32, 1xi32])
        reshape__46, reshape__47 = (lambda x, f: f(x))(paddle._C_ops.reshape_(transpose_11, [x.reshape([]) for x in combine_35]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.depthwise_conv2d: (-1x48x7x7xf32) <- (-1x48x14x14xf32, 48x1x3x3xf32)
        depthwise_conv2d_14 = paddle._C_ops.depthwise_conv2d(reshape__46, parameter_205, [2, 2], [1, 1], 'EXPLICIT', 48, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32, None) <- (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32)
        batch_norm__246, batch_norm__247, batch_norm__248, batch_norm__249, batch_norm__250, batch_norm__251 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_14, parameter_206, parameter_207, parameter_208, parameter_209, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x48x7x7xf32) <- (-1x48x7x7xf32, 48x48x1x1xf32)
        conv2d_27 = paddle._C_ops.conv2d(batch_norm__246, parameter_210, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32, None) <- (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32)
        batch_norm__252, batch_norm__253, batch_norm__254, batch_norm__255, batch_norm__256, batch_norm__257 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_27, parameter_211, parameter_212, parameter_213, parameter_214, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x48x7x7xf32) <- (-1x48x7x7xf32)
        relu__27 = paddle._C_ops.relu_(batch_norm__252)

        # pd_op.conv2d: (-1x48x14x14xf32) <- (-1x48x14x14xf32, 48x48x1x1xf32)
        conv2d_28 = paddle._C_ops.conv2d(reshape__46, parameter_215, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x48x14x14xf32, 48xf32, 48xf32, 48xf32, 48xf32, None) <- (-1x48x14x14xf32, 48xf32, 48xf32, 48xf32, 48xf32)
        batch_norm__258, batch_norm__259, batch_norm__260, batch_norm__261, batch_norm__262, batch_norm__263 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_28, parameter_216, parameter_217, parameter_218, parameter_219, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x48x14x14xf32) <- (-1x48x14x14xf32)
        relu__28 = paddle._C_ops.relu_(batch_norm__258)

        # pd_op.depthwise_conv2d: (-1x48x7x7xf32) <- (-1x48x14x14xf32, 48x1x3x3xf32)
        depthwise_conv2d_15 = paddle._C_ops.depthwise_conv2d(relu__28, parameter_220, [2, 2], [1, 1], 'EXPLICIT', 48, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32, None) <- (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32)
        batch_norm__264, batch_norm__265, batch_norm__266, batch_norm__267, batch_norm__268, batch_norm__269 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_15, parameter_221, parameter_222, parameter_223, parameter_224, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x48x7x7xf32) <- (-1x48x7x7xf32, 48x48x1x1xf32)
        conv2d_29 = paddle._C_ops.conv2d(batch_norm__264, parameter_225, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32, None) <- (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32)
        batch_norm__270, batch_norm__271, batch_norm__272, batch_norm__273, batch_norm__274, batch_norm__275 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_29, parameter_226, parameter_227, parameter_228, parameter_229, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x48x7x7xf32) <- (-1x48x7x7xf32)
        relu__29 = paddle._C_ops.relu_(batch_norm__270)

        # builtin.combine: ([-1x48x7x7xf32, -1x48x7x7xf32]) <- (-1x48x7x7xf32, -1x48x7x7xf32)
        combine_36 = [relu__27, relu__29]

        # pd_op.full: (1xi32) <- ()
        full_106 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.concat: (-1x96x7x7xf32) <- ([-1x48x7x7xf32, -1x48x7x7xf32], 1xi32)
        concat_12 = paddle._C_ops.concat(combine_36, full_106)

        # pd_op.shape: (4xi32) <- (-1x96x7x7xf32)
        shape_12 = paddle._C_ops.shape(concat_12)

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_35 = [0]

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_36 = [1]

        # pd_op.slice: (1xi32) <- (4xi32, 1xi64, 1xi64)
        slice_32 = paddle._C_ops.slice(shape_12, [0], full_int_array_35, full_int_array_36, [1], [0])

        # pd_op.full: (1xi32) <- ()
        full_107 = paddle._C_ops.full([1], float('2'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_108 = paddle._C_ops.full([1], float('48'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_109 = paddle._C_ops.full([1], float('7'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_110 = paddle._C_ops.full([1], float('7'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32, 1xi32)
        combine_37 = [slice_32, full_107, full_108, full_109, full_110]

        # pd_op.reshape_: (-1x2x48x7x7xf32, 0x-1x96x7x7xf32) <- (-1x96x7x7xf32, [1xi32, 1xi32, 1xi32, 1xi32, 1xi32])
        reshape__48, reshape__49 = (lambda x, f: f(x))(paddle._C_ops.reshape_(concat_12, [x.reshape([]) for x in combine_37]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.transpose: (-1x48x2x7x7xf32) <- (-1x2x48x7x7xf32)
        transpose_12 = paddle._C_ops.transpose(reshape__48, [0, 2, 1, 3, 4])

        # pd_op.full: (1xi32) <- ()
        full_111 = paddle._C_ops.full([1], float('96'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_112 = paddle._C_ops.full([1], float('7'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_113 = paddle._C_ops.full([1], float('7'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32)
        combine_38 = [slice_32, full_111, full_112, full_113]

        # pd_op.reshape_: (-1x96x7x7xf32, 0x-1x48x2x7x7xf32) <- (-1x48x2x7x7xf32, [1xi32, 1xi32, 1xi32, 1xi32])
        reshape__50, reshape__51 = (lambda x, f: f(x))(paddle._C_ops.reshape_(transpose_12, [x.reshape([]) for x in combine_38]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_37 = [48, 48]

        # pd_op.full: (1xi32) <- ()
        full_114 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.split: ([-1x48x7x7xf32, -1x48x7x7xf32]) <- (-1x96x7x7xf32, 2xi64, 1xi32)
        split_10 = paddle._C_ops.split(reshape__50, full_int_array_37, full_114)

        # builtin.slice: (-1x48x7x7xf32) <- ([-1x48x7x7xf32, -1x48x7x7xf32])
        slice_33 = split_10[1]

        # pd_op.conv2d: (-1x48x7x7xf32) <- (-1x48x7x7xf32, 48x48x1x1xf32)
        conv2d_30 = paddle._C_ops.conv2d(slice_33, parameter_230, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32, None) <- (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32)
        batch_norm__276, batch_norm__277, batch_norm__278, batch_norm__279, batch_norm__280, batch_norm__281 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_30, parameter_231, parameter_232, parameter_233, parameter_234, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x48x7x7xf32) <- (-1x48x7x7xf32)
        relu__30 = paddle._C_ops.relu_(batch_norm__276)

        # pd_op.depthwise_conv2d: (-1x48x7x7xf32) <- (-1x48x7x7xf32, 48x1x3x3xf32)
        depthwise_conv2d_16 = paddle._C_ops.depthwise_conv2d(relu__30, parameter_235, [1, 1], [1, 1], 'EXPLICIT', 48, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32, None) <- (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32)
        batch_norm__282, batch_norm__283, batch_norm__284, batch_norm__285, batch_norm__286, batch_norm__287 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_16, parameter_236, parameter_237, parameter_238, parameter_239, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x48x7x7xf32) <- (-1x48x7x7xf32, 48x48x1x1xf32)
        conv2d_31 = paddle._C_ops.conv2d(batch_norm__282, parameter_240, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32, None) <- (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32)
        batch_norm__288, batch_norm__289, batch_norm__290, batch_norm__291, batch_norm__292, batch_norm__293 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_31, parameter_241, parameter_242, parameter_243, parameter_244, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x48x7x7xf32) <- (-1x48x7x7xf32)
        relu__31 = paddle._C_ops.relu_(batch_norm__288)

        # builtin.slice: (-1x48x7x7xf32) <- ([-1x48x7x7xf32, -1x48x7x7xf32])
        slice_34 = split_10[0]

        # builtin.combine: ([-1x48x7x7xf32, -1x48x7x7xf32]) <- (-1x48x7x7xf32, -1x48x7x7xf32)
        combine_39 = [slice_34, relu__31]

        # pd_op.full: (1xi32) <- ()
        full_115 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.concat: (-1x96x7x7xf32) <- ([-1x48x7x7xf32, -1x48x7x7xf32], 1xi32)
        concat_13 = paddle._C_ops.concat(combine_39, full_115)

        # pd_op.shape: (4xi32) <- (-1x96x7x7xf32)
        shape_13 = paddle._C_ops.shape(concat_13)

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_38 = [0]

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_39 = [1]

        # pd_op.slice: (1xi32) <- (4xi32, 1xi64, 1xi64)
        slice_35 = paddle._C_ops.slice(shape_13, [0], full_int_array_38, full_int_array_39, [1], [0])

        # pd_op.full: (1xi32) <- ()
        full_116 = paddle._C_ops.full([1], float('2'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_117 = paddle._C_ops.full([1], float('48'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_118 = paddle._C_ops.full([1], float('7'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_119 = paddle._C_ops.full([1], float('7'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32, 1xi32)
        combine_40 = [slice_35, full_116, full_117, full_118, full_119]

        # pd_op.reshape_: (-1x2x48x7x7xf32, 0x-1x96x7x7xf32) <- (-1x96x7x7xf32, [1xi32, 1xi32, 1xi32, 1xi32, 1xi32])
        reshape__52, reshape__53 = (lambda x, f: f(x))(paddle._C_ops.reshape_(concat_13, [x.reshape([]) for x in combine_40]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.transpose: (-1x48x2x7x7xf32) <- (-1x2x48x7x7xf32)
        transpose_13 = paddle._C_ops.transpose(reshape__52, [0, 2, 1, 3, 4])

        # pd_op.full: (1xi32) <- ()
        full_120 = paddle._C_ops.full([1], float('96'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_121 = paddle._C_ops.full([1], float('7'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_122 = paddle._C_ops.full([1], float('7'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32)
        combine_41 = [slice_35, full_120, full_121, full_122]

        # pd_op.reshape_: (-1x96x7x7xf32, 0x-1x48x2x7x7xf32) <- (-1x48x2x7x7xf32, [1xi32, 1xi32, 1xi32, 1xi32])
        reshape__54, reshape__55 = (lambda x, f: f(x))(paddle._C_ops.reshape_(transpose_13, [x.reshape([]) for x in combine_41]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_40 = [48, 48]

        # pd_op.full: (1xi32) <- ()
        full_123 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.split: ([-1x48x7x7xf32, -1x48x7x7xf32]) <- (-1x96x7x7xf32, 2xi64, 1xi32)
        split_11 = paddle._C_ops.split(reshape__54, full_int_array_40, full_123)

        # builtin.slice: (-1x48x7x7xf32) <- ([-1x48x7x7xf32, -1x48x7x7xf32])
        slice_36 = split_11[1]

        # pd_op.conv2d: (-1x48x7x7xf32) <- (-1x48x7x7xf32, 48x48x1x1xf32)
        conv2d_32 = paddle._C_ops.conv2d(slice_36, parameter_245, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32, None) <- (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32)
        batch_norm__294, batch_norm__295, batch_norm__296, batch_norm__297, batch_norm__298, batch_norm__299 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_32, parameter_246, parameter_247, parameter_248, parameter_249, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x48x7x7xf32) <- (-1x48x7x7xf32)
        relu__32 = paddle._C_ops.relu_(batch_norm__294)

        # pd_op.depthwise_conv2d: (-1x48x7x7xf32) <- (-1x48x7x7xf32, 48x1x3x3xf32)
        depthwise_conv2d_17 = paddle._C_ops.depthwise_conv2d(relu__32, parameter_250, [1, 1], [1, 1], 'EXPLICIT', 48, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32, None) <- (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32)
        batch_norm__300, batch_norm__301, batch_norm__302, batch_norm__303, batch_norm__304, batch_norm__305 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_17, parameter_251, parameter_252, parameter_253, parameter_254, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x48x7x7xf32) <- (-1x48x7x7xf32, 48x48x1x1xf32)
        conv2d_33 = paddle._C_ops.conv2d(batch_norm__300, parameter_255, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32, None) <- (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32)
        batch_norm__306, batch_norm__307, batch_norm__308, batch_norm__309, batch_norm__310, batch_norm__311 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_33, parameter_256, parameter_257, parameter_258, parameter_259, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x48x7x7xf32) <- (-1x48x7x7xf32)
        relu__33 = paddle._C_ops.relu_(batch_norm__306)

        # builtin.slice: (-1x48x7x7xf32) <- ([-1x48x7x7xf32, -1x48x7x7xf32])
        slice_37 = split_11[0]

        # builtin.combine: ([-1x48x7x7xf32, -1x48x7x7xf32]) <- (-1x48x7x7xf32, -1x48x7x7xf32)
        combine_42 = [slice_37, relu__33]

        # pd_op.full: (1xi32) <- ()
        full_124 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.concat: (-1x96x7x7xf32) <- ([-1x48x7x7xf32, -1x48x7x7xf32], 1xi32)
        concat_14 = paddle._C_ops.concat(combine_42, full_124)

        # pd_op.shape: (4xi32) <- (-1x96x7x7xf32)
        shape_14 = paddle._C_ops.shape(concat_14)

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_41 = [0]

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_42 = [1]

        # pd_op.slice: (1xi32) <- (4xi32, 1xi64, 1xi64)
        slice_38 = paddle._C_ops.slice(shape_14, [0], full_int_array_41, full_int_array_42, [1], [0])

        # pd_op.full: (1xi32) <- ()
        full_125 = paddle._C_ops.full([1], float('2'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_126 = paddle._C_ops.full([1], float('48'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_127 = paddle._C_ops.full([1], float('7'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_128 = paddle._C_ops.full([1], float('7'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32, 1xi32)
        combine_43 = [slice_38, full_125, full_126, full_127, full_128]

        # pd_op.reshape_: (-1x2x48x7x7xf32, 0x-1x96x7x7xf32) <- (-1x96x7x7xf32, [1xi32, 1xi32, 1xi32, 1xi32, 1xi32])
        reshape__56, reshape__57 = (lambda x, f: f(x))(paddle._C_ops.reshape_(concat_14, [x.reshape([]) for x in combine_43]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.transpose: (-1x48x2x7x7xf32) <- (-1x2x48x7x7xf32)
        transpose_14 = paddle._C_ops.transpose(reshape__56, [0, 2, 1, 3, 4])

        # pd_op.full: (1xi32) <- ()
        full_129 = paddle._C_ops.full([1], float('96'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_130 = paddle._C_ops.full([1], float('7'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_131 = paddle._C_ops.full([1], float('7'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32)
        combine_44 = [slice_38, full_129, full_130, full_131]

        # pd_op.reshape_: (-1x96x7x7xf32, 0x-1x48x2x7x7xf32) <- (-1x48x2x7x7xf32, [1xi32, 1xi32, 1xi32, 1xi32])
        reshape__58, reshape__59 = (lambda x, f: f(x))(paddle._C_ops.reshape_(transpose_14, [x.reshape([]) for x in combine_44]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_43 = [48, 48]

        # pd_op.full: (1xi32) <- ()
        full_132 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.split: ([-1x48x7x7xf32, -1x48x7x7xf32]) <- (-1x96x7x7xf32, 2xi64, 1xi32)
        split_12 = paddle._C_ops.split(reshape__58, full_int_array_43, full_132)

        # builtin.slice: (-1x48x7x7xf32) <- ([-1x48x7x7xf32, -1x48x7x7xf32])
        slice_39 = split_12[1]

        # pd_op.conv2d: (-1x48x7x7xf32) <- (-1x48x7x7xf32, 48x48x1x1xf32)
        conv2d_34 = paddle._C_ops.conv2d(slice_39, parameter_260, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32, None) <- (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32)
        batch_norm__312, batch_norm__313, batch_norm__314, batch_norm__315, batch_norm__316, batch_norm__317 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_34, parameter_261, parameter_262, parameter_263, parameter_264, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x48x7x7xf32) <- (-1x48x7x7xf32)
        relu__34 = paddle._C_ops.relu_(batch_norm__312)

        # pd_op.depthwise_conv2d: (-1x48x7x7xf32) <- (-1x48x7x7xf32, 48x1x3x3xf32)
        depthwise_conv2d_18 = paddle._C_ops.depthwise_conv2d(relu__34, parameter_265, [1, 1], [1, 1], 'EXPLICIT', 48, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32, None) <- (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32)
        batch_norm__318, batch_norm__319, batch_norm__320, batch_norm__321, batch_norm__322, batch_norm__323 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_18, parameter_266, parameter_267, parameter_268, parameter_269, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x48x7x7xf32) <- (-1x48x7x7xf32, 48x48x1x1xf32)
        conv2d_35 = paddle._C_ops.conv2d(batch_norm__318, parameter_270, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32, None) <- (-1x48x7x7xf32, 48xf32, 48xf32, 48xf32, 48xf32)
        batch_norm__324, batch_norm__325, batch_norm__326, batch_norm__327, batch_norm__328, batch_norm__329 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_35, parameter_271, parameter_272, parameter_273, parameter_274, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x48x7x7xf32) <- (-1x48x7x7xf32)
        relu__35 = paddle._C_ops.relu_(batch_norm__324)

        # builtin.slice: (-1x48x7x7xf32) <- ([-1x48x7x7xf32, -1x48x7x7xf32])
        slice_40 = split_12[0]

        # builtin.combine: ([-1x48x7x7xf32, -1x48x7x7xf32]) <- (-1x48x7x7xf32, -1x48x7x7xf32)
        combine_45 = [slice_40, relu__35]

        # pd_op.full: (1xi32) <- ()
        full_133 = paddle._C_ops.full([1], float('1'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.concat: (-1x96x7x7xf32) <- ([-1x48x7x7xf32, -1x48x7x7xf32], 1xi32)
        concat_15 = paddle._C_ops.concat(combine_45, full_133)

        # pd_op.shape: (4xi32) <- (-1x96x7x7xf32)
        shape_15 = paddle._C_ops.shape(concat_15)

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_44 = [0]

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_45 = [1]

        # pd_op.slice: (1xi32) <- (4xi32, 1xi64, 1xi64)
        slice_41 = paddle._C_ops.slice(shape_15, [0], full_int_array_44, full_int_array_45, [1], [0])

        # pd_op.full: (1xi32) <- ()
        full_134 = paddle._C_ops.full([1], float('2'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_135 = paddle._C_ops.full([1], float('48'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_136 = paddle._C_ops.full([1], float('7'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_137 = paddle._C_ops.full([1], float('7'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32, 1xi32)
        combine_46 = [slice_41, full_134, full_135, full_136, full_137]

        # pd_op.reshape_: (-1x2x48x7x7xf32, 0x-1x96x7x7xf32) <- (-1x96x7x7xf32, [1xi32, 1xi32, 1xi32, 1xi32, 1xi32])
        reshape__60, reshape__61 = (lambda x, f: f(x))(paddle._C_ops.reshape_(concat_15, [x.reshape([]) for x in combine_46]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.transpose: (-1x48x2x7x7xf32) <- (-1x2x48x7x7xf32)
        transpose_15 = paddle._C_ops.transpose(reshape__60, [0, 2, 1, 3, 4])

        # pd_op.full: (1xi32) <- ()
        full_138 = paddle._C_ops.full([1], float('96'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_139 = paddle._C_ops.full([1], float('7'), paddle.int32, paddle.core.CPUPlace())

        # pd_op.full: (1xi32) <- ()
        full_140 = paddle._C_ops.full([1], float('7'), paddle.int32, paddle.core.CPUPlace())

        # builtin.combine: ([1xi32, 1xi32, 1xi32, 1xi32]) <- (1xi32, 1xi32, 1xi32, 1xi32)
        combine_47 = [slice_41, full_138, full_139, full_140]

        # pd_op.reshape_: (-1x96x7x7xf32, 0x-1x48x2x7x7xf32) <- (-1x48x2x7x7xf32, [1xi32, 1xi32, 1xi32, 1xi32])
        reshape__62, reshape__63 = (lambda x, f: f(x))(paddle._C_ops.reshape_(transpose_15, [x.reshape([]) for x in combine_47]), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.conv2d: (-1x512x7x7xf32) <- (-1x96x7x7xf32, 512x96x1x1xf32)
        conv2d_36 = paddle._C_ops.conv2d(reshape__62, parameter_275, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x512x7x7xf32, 512xf32, 512xf32, 512xf32, 512xf32, None) <- (-1x512x7x7xf32, 512xf32, 512xf32, 512xf32, 512xf32)
        batch_norm__330, batch_norm__331, batch_norm__332, batch_norm__333, batch_norm__334, batch_norm__335 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_36, parameter_276, parameter_277, parameter_278, parameter_279, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x512x7x7xf32) <- (-1x512x7x7xf32)
        relu__36 = paddle._C_ops.relu_(batch_norm__330)

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_46 = [1, 1]

        # pd_op.pool2d: (-1x512x1x1xf32) <- (-1x512x7x7xf32, 2xi64)
        pool2d_1 = paddle._C_ops.pool2d(relu__36, full_int_array_46, [1, 1], [0, 0], False, True, 'NCHW', 'avg', False, True, 'EXPLICIT')

        # pd_op.flatten_: (-1x512xf32, None) <- (-1x512x1x1xf32)
        flatten__0, flatten__1 = (lambda x, f: f(x))(paddle._C_ops.flatten_(pool2d_1, 1, 3), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.matmul: (-1x1000xf32) <- (-1x512xf32, 512x1000xf32)
        matmul_0 = paddle._C_ops.matmul(flatten__0, parameter_280, False, False)

        # pd_op.add_: (-1x1000xf32) <- (-1x1000xf32, 1000xf32)
        add__0 = paddle._C_ops.add_(matmul_0, parameter_281)

        # pd_op.softmax_: (-1x1000xf32) <- (-1x1000xf32)
        softmax__0 = paddle._C_ops.softmax_(add__0, -1)
        return softmax__0



def GetEnvVarEnableJit():
    enable_jit = os.getenv('PADDLE_DEBUG_ENABLE_JIT')
    return enable_jit not in {
        "0",
        "False",
        "false",
        "OFF",
    }

def GetEnvVarEnableCinn():
    enable_cinn = os.getenv('PADDLE_DEBUG_ENABLE_CINN')
    return enable_cinn not in {
        "0",
        "False",
        "false",
        "OFF",
    }


def GetTolerance(dtype):
    if dtype == np.float16:
        return GetFloat16Tolerance()
    if dtype == np.float32:
        return GetFloat32Tolerance()
    return 1e-6

def GetFloat16Tolerance():
    try:
        return float(os.getenv('PADDLE_DEBUG_FLOAT16_TOL'))
    except:
        return 1e-3

def GetFloat32Tolerance():
    try:
        return float(os.getenv('PADDLE_DEBUG_FLOAT32_TOL'))
    except:
        return 1e-6

def IsInteger(dtype):
    return np.dtype(dtype).char in np.typecodes['AllInteger']


class CinnTestBase:
    def setUp(self):
        paddle.seed(2024)
        self.prepare_data()

    def _test_entry(self):
        dy_outs = self.entry(use_cinn=False)
        cinn_outs = self.entry(use_cinn=GetEnvVarEnableCinn())

        for cinn_out, dy_out in zip(cinn_outs, dy_outs):
          if type(cinn_out) is list and type(dy_out) is list:
            for x, y in zip(cinn_out, dy_out):
              self.assert_all_close(x, y)
          else:
            self.assert_all_close(cinn_out, dy_out)

    def assert_all_close(self, x, y):
        if (hasattr(x, "numpy") and hasattr(y, "numpy")):
            x_numpy = x.numpy()
            y_numpy = y.numpy()
            assert x_numpy.dtype == y_numpy.dtype
            if IsInteger(x_numpy.dtype):
                np.testing.assert_equal(x_numpy, y_numpy)
            else:
                tol = GetTolerance(x_numpy.dtype)
                np.testing.assert_allclose(x_numpy, y_numpy, atol=tol, rtol=tol)
        else:
            assert x == y

class ModuleOp(paddle.nn.Layer, BlockEntries):
    def __init__(self):
        super().__init__()

    def forward(self, parameter_0, parameter_4, parameter_1, parameter_3, parameter_2, parameter_5, parameter_9, parameter_6, parameter_8, parameter_7, parameter_10, parameter_14, parameter_11, parameter_13, parameter_12, parameter_15, parameter_19, parameter_16, parameter_18, parameter_17, parameter_20, parameter_24, parameter_21, parameter_23, parameter_22, parameter_25, parameter_29, parameter_26, parameter_28, parameter_27, parameter_30, parameter_34, parameter_31, parameter_33, parameter_32, parameter_35, parameter_39, parameter_36, parameter_38, parameter_37, parameter_40, parameter_44, parameter_41, parameter_43, parameter_42, parameter_45, parameter_49, parameter_46, parameter_48, parameter_47, parameter_50, parameter_54, parameter_51, parameter_53, parameter_52, parameter_55, parameter_59, parameter_56, parameter_58, parameter_57, parameter_60, parameter_64, parameter_61, parameter_63, parameter_62, parameter_65, parameter_69, parameter_66, parameter_68, parameter_67, parameter_70, parameter_74, parameter_71, parameter_73, parameter_72, parameter_75, parameter_79, parameter_76, parameter_78, parameter_77, parameter_80, parameter_84, parameter_81, parameter_83, parameter_82, parameter_85, parameter_89, parameter_86, parameter_88, parameter_87, parameter_90, parameter_94, parameter_91, parameter_93, parameter_92, parameter_95, parameter_99, parameter_96, parameter_98, parameter_97, parameter_100, parameter_104, parameter_101, parameter_103, parameter_102, parameter_105, parameter_109, parameter_106, parameter_108, parameter_107, parameter_110, parameter_114, parameter_111, parameter_113, parameter_112, parameter_115, parameter_119, parameter_116, parameter_118, parameter_117, parameter_120, parameter_124, parameter_121, parameter_123, parameter_122, parameter_125, parameter_129, parameter_126, parameter_128, parameter_127, parameter_130, parameter_134, parameter_131, parameter_133, parameter_132, parameter_135, parameter_139, parameter_136, parameter_138, parameter_137, parameter_140, parameter_144, parameter_141, parameter_143, parameter_142, parameter_145, parameter_149, parameter_146, parameter_148, parameter_147, parameter_150, parameter_154, parameter_151, parameter_153, parameter_152, parameter_155, parameter_159, parameter_156, parameter_158, parameter_157, parameter_160, parameter_164, parameter_161, parameter_163, parameter_162, parameter_165, parameter_169, parameter_166, parameter_168, parameter_167, parameter_170, parameter_174, parameter_171, parameter_173, parameter_172, parameter_175, parameter_179, parameter_176, parameter_178, parameter_177, parameter_180, parameter_184, parameter_181, parameter_183, parameter_182, parameter_185, parameter_189, parameter_186, parameter_188, parameter_187, parameter_190, parameter_194, parameter_191, parameter_193, parameter_192, parameter_195, parameter_199, parameter_196, parameter_198, parameter_197, parameter_200, parameter_204, parameter_201, parameter_203, parameter_202, parameter_205, parameter_209, parameter_206, parameter_208, parameter_207, parameter_210, parameter_214, parameter_211, parameter_213, parameter_212, parameter_215, parameter_219, parameter_216, parameter_218, parameter_217, parameter_220, parameter_224, parameter_221, parameter_223, parameter_222, parameter_225, parameter_229, parameter_226, parameter_228, parameter_227, parameter_230, parameter_234, parameter_231, parameter_233, parameter_232, parameter_235, parameter_239, parameter_236, parameter_238, parameter_237, parameter_240, parameter_244, parameter_241, parameter_243, parameter_242, parameter_245, parameter_249, parameter_246, parameter_248, parameter_247, parameter_250, parameter_254, parameter_251, parameter_253, parameter_252, parameter_255, parameter_259, parameter_256, parameter_258, parameter_257, parameter_260, parameter_264, parameter_261, parameter_263, parameter_262, parameter_265, parameter_269, parameter_266, parameter_268, parameter_267, parameter_270, parameter_274, parameter_271, parameter_273, parameter_272, parameter_275, parameter_279, parameter_276, parameter_278, parameter_277, parameter_280, parameter_281, feed_0):
        return self.builtin_module_815_0_0(parameter_0, parameter_4, parameter_1, parameter_3, parameter_2, parameter_5, parameter_9, parameter_6, parameter_8, parameter_7, parameter_10, parameter_14, parameter_11, parameter_13, parameter_12, parameter_15, parameter_19, parameter_16, parameter_18, parameter_17, parameter_20, parameter_24, parameter_21, parameter_23, parameter_22, parameter_25, parameter_29, parameter_26, parameter_28, parameter_27, parameter_30, parameter_34, parameter_31, parameter_33, parameter_32, parameter_35, parameter_39, parameter_36, parameter_38, parameter_37, parameter_40, parameter_44, parameter_41, parameter_43, parameter_42, parameter_45, parameter_49, parameter_46, parameter_48, parameter_47, parameter_50, parameter_54, parameter_51, parameter_53, parameter_52, parameter_55, parameter_59, parameter_56, parameter_58, parameter_57, parameter_60, parameter_64, parameter_61, parameter_63, parameter_62, parameter_65, parameter_69, parameter_66, parameter_68, parameter_67, parameter_70, parameter_74, parameter_71, parameter_73, parameter_72, parameter_75, parameter_79, parameter_76, parameter_78, parameter_77, parameter_80, parameter_84, parameter_81, parameter_83, parameter_82, parameter_85, parameter_89, parameter_86, parameter_88, parameter_87, parameter_90, parameter_94, parameter_91, parameter_93, parameter_92, parameter_95, parameter_99, parameter_96, parameter_98, parameter_97, parameter_100, parameter_104, parameter_101, parameter_103, parameter_102, parameter_105, parameter_109, parameter_106, parameter_108, parameter_107, parameter_110, parameter_114, parameter_111, parameter_113, parameter_112, parameter_115, parameter_119, parameter_116, parameter_118, parameter_117, parameter_120, parameter_124, parameter_121, parameter_123, parameter_122, parameter_125, parameter_129, parameter_126, parameter_128, parameter_127, parameter_130, parameter_134, parameter_131, parameter_133, parameter_132, parameter_135, parameter_139, parameter_136, parameter_138, parameter_137, parameter_140, parameter_144, parameter_141, parameter_143, parameter_142, parameter_145, parameter_149, parameter_146, parameter_148, parameter_147, parameter_150, parameter_154, parameter_151, parameter_153, parameter_152, parameter_155, parameter_159, parameter_156, parameter_158, parameter_157, parameter_160, parameter_164, parameter_161, parameter_163, parameter_162, parameter_165, parameter_169, parameter_166, parameter_168, parameter_167, parameter_170, parameter_174, parameter_171, parameter_173, parameter_172, parameter_175, parameter_179, parameter_176, parameter_178, parameter_177, parameter_180, parameter_184, parameter_181, parameter_183, parameter_182, parameter_185, parameter_189, parameter_186, parameter_188, parameter_187, parameter_190, parameter_194, parameter_191, parameter_193, parameter_192, parameter_195, parameter_199, parameter_196, parameter_198, parameter_197, parameter_200, parameter_204, parameter_201, parameter_203, parameter_202, parameter_205, parameter_209, parameter_206, parameter_208, parameter_207, parameter_210, parameter_214, parameter_211, parameter_213, parameter_212, parameter_215, parameter_219, parameter_216, parameter_218, parameter_217, parameter_220, parameter_224, parameter_221, parameter_223, parameter_222, parameter_225, parameter_229, parameter_226, parameter_228, parameter_227, parameter_230, parameter_234, parameter_231, parameter_233, parameter_232, parameter_235, parameter_239, parameter_236, parameter_238, parameter_237, parameter_240, parameter_244, parameter_241, parameter_243, parameter_242, parameter_245, parameter_249, parameter_246, parameter_248, parameter_247, parameter_250, parameter_254, parameter_251, parameter_253, parameter_252, parameter_255, parameter_259, parameter_256, parameter_258, parameter_257, parameter_260, parameter_264, parameter_261, parameter_263, parameter_262, parameter_265, parameter_269, parameter_266, parameter_268, parameter_267, parameter_270, parameter_274, parameter_271, parameter_273, parameter_272, parameter_275, parameter_279, parameter_276, parameter_278, parameter_277, parameter_280, parameter_281, feed_0)

@unittest.skipIf(need_skip, skip_message)
class Test_builtin_module_815_0_0(CinnTestBase, unittest.TestCase):
    def prepare_data(self):
        self.inputs = [
            # parameter_0
            paddle.uniform([24, 3, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_4
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_1
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_3
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_2
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_5
            paddle.uniform([24, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_9
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_6
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_8
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_7
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_10
            paddle.uniform([12, 24, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_14
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_11
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_13
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_12
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_15
            paddle.uniform([12, 24, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_19
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_16
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_18
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_17
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_20
            paddle.uniform([12, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_24
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_21
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_23
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_22
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_25
            paddle.uniform([12, 12, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_29
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_26
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_28
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_27
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_30
            paddle.uniform([12, 12, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_34
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_31
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_33
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_32
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_35
            paddle.uniform([12, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_39
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_36
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_38
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_37
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_40
            paddle.uniform([12, 12, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_44
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_41
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_43
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_42
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_45
            paddle.uniform([12, 12, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_49
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_46
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_48
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_47
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_50
            paddle.uniform([12, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_54
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_51
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_53
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_52
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_55
            paddle.uniform([12, 12, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_59
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_56
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_58
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_57
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_60
            paddle.uniform([12, 12, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_64
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_61
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_63
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_62
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_65
            paddle.uniform([12, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_69
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_66
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_68
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_67
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_70
            paddle.uniform([12, 12, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_74
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_71
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_73
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_72
            paddle.uniform([12], dtype='float32', min=0, max=0.5),
            # parameter_75
            paddle.uniform([24, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_79
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_76
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_78
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_77
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_80
            paddle.uniform([24, 24, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_84
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_81
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_83
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_82
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_85
            paddle.uniform([24, 24, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_89
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_86
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_88
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_87
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_90
            paddle.uniform([24, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_94
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_91
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_93
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_92
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_95
            paddle.uniform([24, 24, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_99
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_96
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_98
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_97
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_100
            paddle.uniform([24, 24, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_104
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_101
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_103
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_102
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_105
            paddle.uniform([24, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_109
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_106
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_108
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_107
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_110
            paddle.uniform([24, 24, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_114
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_111
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_113
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_112
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_115
            paddle.uniform([24, 24, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_119
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_116
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_118
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_117
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_120
            paddle.uniform([24, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_124
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_121
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_123
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_122
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_125
            paddle.uniform([24, 24, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_129
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_126
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_128
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_127
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_130
            paddle.uniform([24, 24, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_134
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_131
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_133
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_132
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_135
            paddle.uniform([24, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_139
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_136
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_138
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_137
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_140
            paddle.uniform([24, 24, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_144
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_141
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_143
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_142
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_145
            paddle.uniform([24, 24, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_149
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_146
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_148
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_147
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_150
            paddle.uniform([24, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_154
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_151
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_153
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_152
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_155
            paddle.uniform([24, 24, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_159
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_156
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_158
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_157
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_160
            paddle.uniform([24, 24, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_164
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_161
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_163
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_162
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_165
            paddle.uniform([24, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_169
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_166
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_168
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_167
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_170
            paddle.uniform([24, 24, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_174
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_171
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_173
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_172
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_175
            paddle.uniform([24, 24, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_179
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_176
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_178
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_177
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_180
            paddle.uniform([24, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_184
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_181
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_183
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_182
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_185
            paddle.uniform([24, 24, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_189
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_186
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_188
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_187
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_190
            paddle.uniform([24, 24, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_194
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_191
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_193
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_192
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_195
            paddle.uniform([24, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_199
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_196
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_198
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_197
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_200
            paddle.uniform([24, 24, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_204
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_201
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_203
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_202
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_205
            paddle.uniform([48, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_209
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_206
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_208
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_207
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_210
            paddle.uniform([48, 48, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_214
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_211
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_213
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_212
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_215
            paddle.uniform([48, 48, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_219
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_216
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_218
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_217
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_220
            paddle.uniform([48, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_224
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_221
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_223
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_222
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_225
            paddle.uniform([48, 48, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_229
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_226
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_228
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_227
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_230
            paddle.uniform([48, 48, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_234
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_231
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_233
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_232
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_235
            paddle.uniform([48, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_239
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_236
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_238
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_237
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_240
            paddle.uniform([48, 48, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_244
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_241
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_243
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_242
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_245
            paddle.uniform([48, 48, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_249
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_246
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_248
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_247
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_250
            paddle.uniform([48, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_254
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_251
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_253
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_252
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_255
            paddle.uniform([48, 48, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_259
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_256
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_258
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_257
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_260
            paddle.uniform([48, 48, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_264
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_261
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_263
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_262
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_265
            paddle.uniform([48, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_269
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_266
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_268
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_267
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_270
            paddle.uniform([48, 48, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_274
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_271
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_273
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_272
            paddle.uniform([48], dtype='float32', min=0, max=0.5),
            # parameter_275
            paddle.uniform([512, 96, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_279
            paddle.uniform([512], dtype='float32', min=0, max=0.5),
            # parameter_276
            paddle.uniform([512], dtype='float32', min=0, max=0.5),
            # parameter_278
            paddle.uniform([512], dtype='float32', min=0, max=0.5),
            # parameter_277
            paddle.uniform([512], dtype='float32', min=0, max=0.5),
            # parameter_280
            paddle.uniform([512, 1000], dtype='float32', min=0, max=0.5),
            # parameter_281
            paddle.uniform([1000], dtype='float32', min=0, max=0.5),
            # feed_0
            paddle.uniform([1, 3, 224, 224], dtype='float32', min=0, max=0.5),
        ]
        for input in self.inputs:
            input.stop_gradient = True

    def apply_to_static(self, net, use_cinn):
        build_strategy = paddle.static.BuildStrategy()
        input_spec = [
            # parameter_0
            paddle.static.InputSpec(shape=[24, 3, 3, 3], dtype='float32'),
            # parameter_4
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_1
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_3
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_2
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_5
            paddle.static.InputSpec(shape=[24, 1, 3, 3], dtype='float32'),
            # parameter_9
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_6
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_8
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_7
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_10
            paddle.static.InputSpec(shape=[12, 24, 1, 1], dtype='float32'),
            # parameter_14
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_11
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_13
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_12
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_15
            paddle.static.InputSpec(shape=[12, 24, 1, 1], dtype='float32'),
            # parameter_19
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_16
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_18
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_17
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_20
            paddle.static.InputSpec(shape=[12, 1, 3, 3], dtype='float32'),
            # parameter_24
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_21
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_23
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_22
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_25
            paddle.static.InputSpec(shape=[12, 12, 1, 1], dtype='float32'),
            # parameter_29
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_26
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_28
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_27
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_30
            paddle.static.InputSpec(shape=[12, 12, 1, 1], dtype='float32'),
            # parameter_34
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_31
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_33
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_32
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_35
            paddle.static.InputSpec(shape=[12, 1, 3, 3], dtype='float32'),
            # parameter_39
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_36
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_38
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_37
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_40
            paddle.static.InputSpec(shape=[12, 12, 1, 1], dtype='float32'),
            # parameter_44
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_41
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_43
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_42
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_45
            paddle.static.InputSpec(shape=[12, 12, 1, 1], dtype='float32'),
            # parameter_49
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_46
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_48
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_47
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_50
            paddle.static.InputSpec(shape=[12, 1, 3, 3], dtype='float32'),
            # parameter_54
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_51
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_53
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_52
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_55
            paddle.static.InputSpec(shape=[12, 12, 1, 1], dtype='float32'),
            # parameter_59
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_56
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_58
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_57
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_60
            paddle.static.InputSpec(shape=[12, 12, 1, 1], dtype='float32'),
            # parameter_64
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_61
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_63
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_62
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_65
            paddle.static.InputSpec(shape=[12, 1, 3, 3], dtype='float32'),
            # parameter_69
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_66
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_68
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_67
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_70
            paddle.static.InputSpec(shape=[12, 12, 1, 1], dtype='float32'),
            # parameter_74
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_71
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_73
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_72
            paddle.static.InputSpec(shape=[12], dtype='float32'),
            # parameter_75
            paddle.static.InputSpec(shape=[24, 1, 3, 3], dtype='float32'),
            # parameter_79
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_76
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_78
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_77
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_80
            paddle.static.InputSpec(shape=[24, 24, 1, 1], dtype='float32'),
            # parameter_84
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_81
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_83
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_82
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_85
            paddle.static.InputSpec(shape=[24, 24, 1, 1], dtype='float32'),
            # parameter_89
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_86
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_88
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_87
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_90
            paddle.static.InputSpec(shape=[24, 1, 3, 3], dtype='float32'),
            # parameter_94
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_91
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_93
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_92
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_95
            paddle.static.InputSpec(shape=[24, 24, 1, 1], dtype='float32'),
            # parameter_99
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_96
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_98
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_97
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_100
            paddle.static.InputSpec(shape=[24, 24, 1, 1], dtype='float32'),
            # parameter_104
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_101
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_103
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_102
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_105
            paddle.static.InputSpec(shape=[24, 1, 3, 3], dtype='float32'),
            # parameter_109
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_106
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_108
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_107
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_110
            paddle.static.InputSpec(shape=[24, 24, 1, 1], dtype='float32'),
            # parameter_114
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_111
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_113
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_112
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_115
            paddle.static.InputSpec(shape=[24, 24, 1, 1], dtype='float32'),
            # parameter_119
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_116
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_118
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_117
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_120
            paddle.static.InputSpec(shape=[24, 1, 3, 3], dtype='float32'),
            # parameter_124
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_121
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_123
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_122
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_125
            paddle.static.InputSpec(shape=[24, 24, 1, 1], dtype='float32'),
            # parameter_129
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_126
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_128
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_127
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_130
            paddle.static.InputSpec(shape=[24, 24, 1, 1], dtype='float32'),
            # parameter_134
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_131
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_133
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_132
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_135
            paddle.static.InputSpec(shape=[24, 1, 3, 3], dtype='float32'),
            # parameter_139
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_136
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_138
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_137
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_140
            paddle.static.InputSpec(shape=[24, 24, 1, 1], dtype='float32'),
            # parameter_144
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_141
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_143
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_142
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_145
            paddle.static.InputSpec(shape=[24, 24, 1, 1], dtype='float32'),
            # parameter_149
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_146
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_148
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_147
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_150
            paddle.static.InputSpec(shape=[24, 1, 3, 3], dtype='float32'),
            # parameter_154
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_151
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_153
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_152
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_155
            paddle.static.InputSpec(shape=[24, 24, 1, 1], dtype='float32'),
            # parameter_159
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_156
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_158
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_157
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_160
            paddle.static.InputSpec(shape=[24, 24, 1, 1], dtype='float32'),
            # parameter_164
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_161
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_163
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_162
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_165
            paddle.static.InputSpec(shape=[24, 1, 3, 3], dtype='float32'),
            # parameter_169
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_166
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_168
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_167
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_170
            paddle.static.InputSpec(shape=[24, 24, 1, 1], dtype='float32'),
            # parameter_174
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_171
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_173
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_172
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_175
            paddle.static.InputSpec(shape=[24, 24, 1, 1], dtype='float32'),
            # parameter_179
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_176
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_178
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_177
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_180
            paddle.static.InputSpec(shape=[24, 1, 3, 3], dtype='float32'),
            # parameter_184
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_181
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_183
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_182
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_185
            paddle.static.InputSpec(shape=[24, 24, 1, 1], dtype='float32'),
            # parameter_189
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_186
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_188
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_187
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_190
            paddle.static.InputSpec(shape=[24, 24, 1, 1], dtype='float32'),
            # parameter_194
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_191
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_193
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_192
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_195
            paddle.static.InputSpec(shape=[24, 1, 3, 3], dtype='float32'),
            # parameter_199
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_196
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_198
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_197
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_200
            paddle.static.InputSpec(shape=[24, 24, 1, 1], dtype='float32'),
            # parameter_204
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_201
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_203
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_202
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_205
            paddle.static.InputSpec(shape=[48, 1, 3, 3], dtype='float32'),
            # parameter_209
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_206
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_208
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_207
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_210
            paddle.static.InputSpec(shape=[48, 48, 1, 1], dtype='float32'),
            # parameter_214
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_211
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_213
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_212
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_215
            paddle.static.InputSpec(shape=[48, 48, 1, 1], dtype='float32'),
            # parameter_219
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_216
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_218
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_217
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_220
            paddle.static.InputSpec(shape=[48, 1, 3, 3], dtype='float32'),
            # parameter_224
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_221
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_223
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_222
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_225
            paddle.static.InputSpec(shape=[48, 48, 1, 1], dtype='float32'),
            # parameter_229
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_226
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_228
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_227
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_230
            paddle.static.InputSpec(shape=[48, 48, 1, 1], dtype='float32'),
            # parameter_234
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_231
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_233
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_232
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_235
            paddle.static.InputSpec(shape=[48, 1, 3, 3], dtype='float32'),
            # parameter_239
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_236
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_238
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_237
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_240
            paddle.static.InputSpec(shape=[48, 48, 1, 1], dtype='float32'),
            # parameter_244
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_241
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_243
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_242
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_245
            paddle.static.InputSpec(shape=[48, 48, 1, 1], dtype='float32'),
            # parameter_249
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_246
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_248
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_247
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_250
            paddle.static.InputSpec(shape=[48, 1, 3, 3], dtype='float32'),
            # parameter_254
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_251
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_253
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_252
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_255
            paddle.static.InputSpec(shape=[48, 48, 1, 1], dtype='float32'),
            # parameter_259
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_256
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_258
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_257
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_260
            paddle.static.InputSpec(shape=[48, 48, 1, 1], dtype='float32'),
            # parameter_264
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_261
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_263
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_262
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_265
            paddle.static.InputSpec(shape=[48, 1, 3, 3], dtype='float32'),
            # parameter_269
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_266
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_268
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_267
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_270
            paddle.static.InputSpec(shape=[48, 48, 1, 1], dtype='float32'),
            # parameter_274
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_271
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_273
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_272
            paddle.static.InputSpec(shape=[48], dtype='float32'),
            # parameter_275
            paddle.static.InputSpec(shape=[512, 96, 1, 1], dtype='float32'),
            # parameter_279
            paddle.static.InputSpec(shape=[512], dtype='float32'),
            # parameter_276
            paddle.static.InputSpec(shape=[512], dtype='float32'),
            # parameter_278
            paddle.static.InputSpec(shape=[512], dtype='float32'),
            # parameter_277
            paddle.static.InputSpec(shape=[512], dtype='float32'),
            # parameter_280
            paddle.static.InputSpec(shape=[512, 1000], dtype='float32'),
            # parameter_281
            paddle.static.InputSpec(shape=[1000], dtype='float32'),
            # feed_0
            paddle.static.InputSpec(shape=[None, 3, 224, 224], dtype='float32'),
        ]
        build_strategy.build_cinn_pass = use_cinn
        return paddle.jit.to_static(
            net,
            input_spec=input_spec,
            build_strategy=build_strategy,
            full_graph=True,
        )

    def entry(self, use_cinn):
        net = ModuleOp()
        if GetEnvVarEnableJit():
            net = self.apply_to_static(net, use_cinn)
        paddle.seed(2024)
        out = net(*self.inputs)
        return out

    def test_entry(self):
        if AthenaTryRunEnabled():
            if try_run_exit_code == 0:
                # All unittest cases passed.
                return
            if try_run_exit_code < 0:
                # program panicked.
                raise RuntimeError(f"panicked. panic stderr have been reported by the unittest `TestTryRun.test_panic`.")
        self._test_entry()

if __name__ == '__main__':
    unittest.main()