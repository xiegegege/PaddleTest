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
    return [369][block_idx] - 1 # number-of-ops-in-block

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
    def builtin_module_745_0_0(self, parameter_0, parameter_4, parameter_1, parameter_3, parameter_2, parameter_5, parameter_9, parameter_6, parameter_8, parameter_7, parameter_10, parameter_14, parameter_11, parameter_13, parameter_12, parameter_15, parameter_19, parameter_16, parameter_18, parameter_17, parameter_20, parameter_24, parameter_21, parameter_23, parameter_22, parameter_25, parameter_29, parameter_26, parameter_28, parameter_27, parameter_30, parameter_34, parameter_31, parameter_33, parameter_32, parameter_35, parameter_39, parameter_36, parameter_38, parameter_37, parameter_40, parameter_44, parameter_41, parameter_43, parameter_42, parameter_45, parameter_49, parameter_46, parameter_48, parameter_47, parameter_50, parameter_54, parameter_51, parameter_53, parameter_52, parameter_55, parameter_59, parameter_56, parameter_58, parameter_57, parameter_60, parameter_61, parameter_62, parameter_63, parameter_64, parameter_68, parameter_65, parameter_67, parameter_66, parameter_69, parameter_73, parameter_70, parameter_72, parameter_71, parameter_74, parameter_78, parameter_75, parameter_77, parameter_76, parameter_79, parameter_80, parameter_81, parameter_82, parameter_83, parameter_87, parameter_84, parameter_86, parameter_85, parameter_88, parameter_92, parameter_89, parameter_91, parameter_90, parameter_93, parameter_97, parameter_94, parameter_96, parameter_95, parameter_98, parameter_99, parameter_100, parameter_101, parameter_102, parameter_106, parameter_103, parameter_105, parameter_104, parameter_107, parameter_111, parameter_108, parameter_110, parameter_109, parameter_112, parameter_116, parameter_113, parameter_115, parameter_114, parameter_117, parameter_121, parameter_118, parameter_120, parameter_119, parameter_122, parameter_126, parameter_123, parameter_125, parameter_124, parameter_127, parameter_131, parameter_128, parameter_130, parameter_129, parameter_132, parameter_136, parameter_133, parameter_135, parameter_134, parameter_137, parameter_141, parameter_138, parameter_140, parameter_139, parameter_142, parameter_146, parameter_143, parameter_145, parameter_144, parameter_147, parameter_151, parameter_148, parameter_150, parameter_149, parameter_152, parameter_156, parameter_153, parameter_155, parameter_154, parameter_157, parameter_161, parameter_158, parameter_160, parameter_159, parameter_162, parameter_166, parameter_163, parameter_165, parameter_164, parameter_167, parameter_171, parameter_168, parameter_170, parameter_169, parameter_172, parameter_176, parameter_173, parameter_175, parameter_174, parameter_177, parameter_178, parameter_179, parameter_180, parameter_181, parameter_185, parameter_182, parameter_184, parameter_183, parameter_186, parameter_190, parameter_187, parameter_189, parameter_188, parameter_191, parameter_195, parameter_192, parameter_194, parameter_193, parameter_196, parameter_197, parameter_198, parameter_199, parameter_200, parameter_204, parameter_201, parameter_203, parameter_202, parameter_205, parameter_209, parameter_206, parameter_208, parameter_207, parameter_210, parameter_214, parameter_211, parameter_213, parameter_212, parameter_215, parameter_216, parameter_217, parameter_218, parameter_219, parameter_223, parameter_220, parameter_222, parameter_221, parameter_224, parameter_228, parameter_225, parameter_227, parameter_226, parameter_229, parameter_233, parameter_230, parameter_232, parameter_231, parameter_234, parameter_235, parameter_236, parameter_237, parameter_238, parameter_242, parameter_239, parameter_241, parameter_240, parameter_243, parameter_247, parameter_244, parameter_246, parameter_245, parameter_248, parameter_252, parameter_249, parameter_251, parameter_250, parameter_253, parameter_254, parameter_255, parameter_256, parameter_257, parameter_261, parameter_258, parameter_260, parameter_259, parameter_262, parameter_263, parameter_267, parameter_264, parameter_266, parameter_265, parameter_268, parameter_269, parameter_273, parameter_270, parameter_272, parameter_271, parameter_274, parameter_275, parameter_279, parameter_276, parameter_278, parameter_277, parameter_280, parameter_284, parameter_281, parameter_283, parameter_282, parameter_285, parameter_286, parameter_290, parameter_287, parameter_289, parameter_288, parameter_291, parameter_295, parameter_292, parameter_294, parameter_293, parameter_296, parameter_297, parameter_301, parameter_298, parameter_300, parameter_299, parameter_302, parameter_306, parameter_303, parameter_305, parameter_304, parameter_307, parameter_308, parameter_312, parameter_309, parameter_311, parameter_310, parameter_313, parameter_317, parameter_314, parameter_316, parameter_315, parameter_318, parameter_319, parameter_323, parameter_320, parameter_322, parameter_321, parameter_324, parameter_328, parameter_325, parameter_327, parameter_326, parameter_329, parameter_330, parameter_334, parameter_331, parameter_333, parameter_332, parameter_335, parameter_339, parameter_336, parameter_338, parameter_337, parameter_340, parameter_341, parameter_345, parameter_342, parameter_344, parameter_343, parameter_346, parameter_350, parameter_347, parameter_349, parameter_348, parameter_351, parameter_352, parameter_356, parameter_353, parameter_355, parameter_354, parameter_357, parameter_361, parameter_358, parameter_360, parameter_359, parameter_362, parameter_363, parameter_367, parameter_364, parameter_366, parameter_365, parameter_368, feed_0):

        # pd_op.shape: (4xi32) <- (-1x3x-1x-1xf32)
        shape_0 = paddle._C_ops.shape(feed_0)

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_0 = [2]

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_1 = [2147483647]

        # pd_op.slice: (2xi32) <- (4xi32, 1xi64, 1xi64)
        slice_0 = paddle._C_ops.slice(shape_0, [0], full_int_array_0, full_int_array_1, [1], [])

        # pd_op.conv2d: (-1x16x-1x-1xf32) <- (-1x3x-1x-1xf32, 16x3x3x3xf32)
        conv2d_0 = paddle._C_ops.conv2d(feed_0, parameter_0, [2, 2], [1, 1], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x16x-1x-1xf32, 16xf32, 16xf32, 16xf32, 16xf32, None) <- (-1x16x-1x-1xf32, 16xf32, 16xf32, 16xf32, 16xf32)
        batch_norm__0, batch_norm__1, batch_norm__2, batch_norm__3, batch_norm__4, batch_norm__5 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_0, parameter_1, parameter_2, parameter_3, parameter_4, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x16x-1x-1xf32) <- (-1x16x-1x-1xf32)
        hardswish_0 = paddle._C_ops.hardswish(batch_norm__0)

        # pd_op.conv2d: (-1x16x-1x-1xf32) <- (-1x16x-1x-1xf32, 16x16x1x1xf32)
        conv2d_1 = paddle._C_ops.conv2d(hardswish_0, parameter_5, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x16x-1x-1xf32, 16xf32, 16xf32, 16xf32, 16xf32, None) <- (-1x16x-1x-1xf32, 16xf32, 16xf32, 16xf32, 16xf32)
        batch_norm__6, batch_norm__7, batch_norm__8, batch_norm__9, batch_norm__10, batch_norm__11 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_1, parameter_6, parameter_7, parameter_8, parameter_9, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu: (-1x16x-1x-1xf32) <- (-1x16x-1x-1xf32)
        relu_0 = paddle._C_ops.relu(batch_norm__6)

        # pd_op.depthwise_conv2d: (-1x16x-1x-1xf32) <- (-1x16x-1x-1xf32, 16x1x3x3xf32)
        depthwise_conv2d_0 = paddle._C_ops.depthwise_conv2d(relu_0, parameter_10, [1, 1], [1, 1], 'EXPLICIT', 16, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x16x-1x-1xf32, 16xf32, 16xf32, 16xf32, 16xf32, None) <- (-1x16x-1x-1xf32, 16xf32, 16xf32, 16xf32, 16xf32)
        batch_norm__12, batch_norm__13, batch_norm__14, batch_norm__15, batch_norm__16, batch_norm__17 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_0, parameter_11, parameter_12, parameter_13, parameter_14, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu: (-1x16x-1x-1xf32) <- (-1x16x-1x-1xf32)
        relu_1 = paddle._C_ops.relu(batch_norm__12)

        # pd_op.conv2d: (-1x16x-1x-1xf32) <- (-1x16x-1x-1xf32, 16x16x1x1xf32)
        conv2d_2 = paddle._C_ops.conv2d(relu_1, parameter_15, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x16x-1x-1xf32, 16xf32, 16xf32, 16xf32, 16xf32, None) <- (-1x16x-1x-1xf32, 16xf32, 16xf32, 16xf32, 16xf32)
        batch_norm__18, batch_norm__19, batch_norm__20, batch_norm__21, batch_norm__22, batch_norm__23 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_2, parameter_16, parameter_17, parameter_18, parameter_19, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.add: (-1x16x-1x-1xf32) <- (-1x16x-1x-1xf32, -1x16x-1x-1xf32)
        add_0 = paddle._C_ops.add(hardswish_0, batch_norm__18)

        # pd_op.conv2d: (-1x64x-1x-1xf32) <- (-1x16x-1x-1xf32, 64x16x1x1xf32)
        conv2d_3 = paddle._C_ops.conv2d(add_0, parameter_20, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x64x-1x-1xf32, 64xf32, 64xf32, 64xf32, 64xf32, None) <- (-1x64x-1x-1xf32, 64xf32, 64xf32, 64xf32, 64xf32)
        batch_norm__24, batch_norm__25, batch_norm__26, batch_norm__27, batch_norm__28, batch_norm__29 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_3, parameter_21, parameter_22, parameter_23, parameter_24, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu: (-1x64x-1x-1xf32) <- (-1x64x-1x-1xf32)
        relu_2 = paddle._C_ops.relu(batch_norm__24)

        # pd_op.depthwise_conv2d: (-1x64x-1x-1xf32) <- (-1x64x-1x-1xf32, 64x1x3x3xf32)
        depthwise_conv2d_1 = paddle._C_ops.depthwise_conv2d(relu_2, parameter_25, [2, 2], [1, 1], 'EXPLICIT', 64, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x64x-1x-1xf32, 64xf32, 64xf32, 64xf32, 64xf32, None) <- (-1x64x-1x-1xf32, 64xf32, 64xf32, 64xf32, 64xf32)
        batch_norm__30, batch_norm__31, batch_norm__32, batch_norm__33, batch_norm__34, batch_norm__35 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_1, parameter_26, parameter_27, parameter_28, parameter_29, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu: (-1x64x-1x-1xf32) <- (-1x64x-1x-1xf32)
        relu_3 = paddle._C_ops.relu(batch_norm__30)

        # pd_op.conv2d: (-1x24x-1x-1xf32) <- (-1x64x-1x-1xf32, 24x64x1x1xf32)
        conv2d_4 = paddle._C_ops.conv2d(relu_3, parameter_30, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x-1x-1xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x-1x-1xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__36, batch_norm__37, batch_norm__38, batch_norm__39, batch_norm__40, batch_norm__41 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_4, parameter_31, parameter_32, parameter_33, parameter_34, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x72x-1x-1xf32) <- (-1x24x-1x-1xf32, 72x24x1x1xf32)
        conv2d_5 = paddle._C_ops.conv2d(batch_norm__36, parameter_35, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x72x-1x-1xf32, 72xf32, 72xf32, 72xf32, 72xf32, None) <- (-1x72x-1x-1xf32, 72xf32, 72xf32, 72xf32, 72xf32)
        batch_norm__42, batch_norm__43, batch_norm__44, batch_norm__45, batch_norm__46, batch_norm__47 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_5, parameter_36, parameter_37, parameter_38, parameter_39, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu: (-1x72x-1x-1xf32) <- (-1x72x-1x-1xf32)
        relu_4 = paddle._C_ops.relu(batch_norm__42)

        # pd_op.depthwise_conv2d: (-1x72x-1x-1xf32) <- (-1x72x-1x-1xf32, 72x1x3x3xf32)
        depthwise_conv2d_2 = paddle._C_ops.depthwise_conv2d(relu_4, parameter_40, [1, 1], [1, 1], 'EXPLICIT', 72, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x72x-1x-1xf32, 72xf32, 72xf32, 72xf32, 72xf32, None) <- (-1x72x-1x-1xf32, 72xf32, 72xf32, 72xf32, 72xf32)
        batch_norm__48, batch_norm__49, batch_norm__50, batch_norm__51, batch_norm__52, batch_norm__53 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_2, parameter_41, parameter_42, parameter_43, parameter_44, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu: (-1x72x-1x-1xf32) <- (-1x72x-1x-1xf32)
        relu_5 = paddle._C_ops.relu(batch_norm__48)

        # pd_op.conv2d: (-1x24x-1x-1xf32) <- (-1x72x-1x-1xf32, 24x72x1x1xf32)
        conv2d_6 = paddle._C_ops.conv2d(relu_5, parameter_45, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x-1x-1xf32, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x-1x-1xf32, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__54, batch_norm__55, batch_norm__56, batch_norm__57, batch_norm__58, batch_norm__59 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_6, parameter_46, parameter_47, parameter_48, parameter_49, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.add: (-1x24x-1x-1xf32) <- (-1x24x-1x-1xf32, -1x24x-1x-1xf32)
        add_1 = paddle._C_ops.add(batch_norm__36, batch_norm__54)

        # pd_op.conv2d: (-1x72x-1x-1xf32) <- (-1x24x-1x-1xf32, 72x24x1x1xf32)
        conv2d_7 = paddle._C_ops.conv2d(add_1, parameter_50, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x72x-1x-1xf32, 72xf32, 72xf32, 72xf32, 72xf32, None) <- (-1x72x-1x-1xf32, 72xf32, 72xf32, 72xf32, 72xf32)
        batch_norm__60, batch_norm__61, batch_norm__62, batch_norm__63, batch_norm__64, batch_norm__65 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_7, parameter_51, parameter_52, parameter_53, parameter_54, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu: (-1x72x-1x-1xf32) <- (-1x72x-1x-1xf32)
        relu_6 = paddle._C_ops.relu(batch_norm__60)

        # pd_op.depthwise_conv2d: (-1x72x-1x-1xf32) <- (-1x72x-1x-1xf32, 72x1x5x5xf32)
        depthwise_conv2d_3 = paddle._C_ops.depthwise_conv2d(relu_6, parameter_55, [2, 2], [2, 2], 'EXPLICIT', 72, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x72x-1x-1xf32, 72xf32, 72xf32, 72xf32, 72xf32, None) <- (-1x72x-1x-1xf32, 72xf32, 72xf32, 72xf32, 72xf32)
        batch_norm__66, batch_norm__67, batch_norm__68, batch_norm__69, batch_norm__70, batch_norm__71 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_3, parameter_56, parameter_57, parameter_58, parameter_59, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu: (-1x72x-1x-1xf32) <- (-1x72x-1x-1xf32)
        relu_7 = paddle._C_ops.relu(batch_norm__66)

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_2 = [1, 1]

        # pd_op.pool2d: (-1x72x1x1xf32) <- (-1x72x-1x-1xf32, 2xi64)
        pool2d_0 = paddle._C_ops.pool2d(relu_7, full_int_array_2, [1, 1], [0, 0], False, True, 'NCHW', 'avg', False, True, 'EXPLICIT')

        # pd_op.conv2d: (-1x18x1x1xf32) <- (-1x72x1x1xf32, 18x72x1x1xf32)
        conv2d_8 = paddle._C_ops.conv2d(pool2d_0, parameter_60, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_3 = [1, 18, 1, 1]

        # pd_op.reshape: (1x18x1x1xf32, 0x18xf32) <- (18xf32, 4xi64)
        reshape_0, reshape_1 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_61, full_int_array_3), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add_: (-1x18x1x1xf32) <- (-1x18x1x1xf32, 1x18x1x1xf32)
        add__0 = paddle._C_ops.add_(conv2d_8, reshape_0)

        # pd_op.relu_: (-1x18x1x1xf32) <- (-1x18x1x1xf32)
        relu__0 = paddle._C_ops.relu_(add__0)

        # pd_op.conv2d: (-1x72x1x1xf32) <- (-1x18x1x1xf32, 72x18x1x1xf32)
        conv2d_9 = paddle._C_ops.conv2d(relu__0, parameter_62, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_4 = [1, 72, 1, 1]

        # pd_op.reshape: (1x72x1x1xf32, 0x72xf32) <- (72xf32, 4xi64)
        reshape_2, reshape_3 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_63, full_int_array_4), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add_: (-1x72x1x1xf32) <- (-1x72x1x1xf32, 1x72x1x1xf32)
        add__1 = paddle._C_ops.add_(conv2d_9, reshape_2)

        # pd_op.hardsigmoid: (-1x72x1x1xf32) <- (-1x72x1x1xf32)
        hardsigmoid_0 = paddle._C_ops.hardsigmoid(add__1, float('0.2'), float('0.5'))

        # pd_op.multiply: (-1x72x-1x-1xf32) <- (-1x72x-1x-1xf32, -1x72x1x1xf32)
        multiply_0 = paddle._C_ops.multiply(relu_7, hardsigmoid_0)

        # pd_op.conv2d: (-1x40x-1x-1xf32) <- (-1x72x-1x-1xf32, 40x72x1x1xf32)
        conv2d_10 = paddle._C_ops.conv2d(multiply_0, parameter_64, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x40x-1x-1xf32, 40xf32, 40xf32, 40xf32, 40xf32, None) <- (-1x40x-1x-1xf32, 40xf32, 40xf32, 40xf32, 40xf32)
        batch_norm__72, batch_norm__73, batch_norm__74, batch_norm__75, batch_norm__76, batch_norm__77 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_10, parameter_65, parameter_66, parameter_67, parameter_68, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x120x-1x-1xf32) <- (-1x40x-1x-1xf32, 120x40x1x1xf32)
        conv2d_11 = paddle._C_ops.conv2d(batch_norm__72, parameter_69, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x120x-1x-1xf32, 120xf32, 120xf32, 120xf32, 120xf32, None) <- (-1x120x-1x-1xf32, 120xf32, 120xf32, 120xf32, 120xf32)
        batch_norm__78, batch_norm__79, batch_norm__80, batch_norm__81, batch_norm__82, batch_norm__83 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_11, parameter_70, parameter_71, parameter_72, parameter_73, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu: (-1x120x-1x-1xf32) <- (-1x120x-1x-1xf32)
        relu_8 = paddle._C_ops.relu(batch_norm__78)

        # pd_op.depthwise_conv2d: (-1x120x-1x-1xf32) <- (-1x120x-1x-1xf32, 120x1x5x5xf32)
        depthwise_conv2d_4 = paddle._C_ops.depthwise_conv2d(relu_8, parameter_74, [1, 1], [2, 2], 'EXPLICIT', 120, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x120x-1x-1xf32, 120xf32, 120xf32, 120xf32, 120xf32, None) <- (-1x120x-1x-1xf32, 120xf32, 120xf32, 120xf32, 120xf32)
        batch_norm__84, batch_norm__85, batch_norm__86, batch_norm__87, batch_norm__88, batch_norm__89 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_4, parameter_75, parameter_76, parameter_77, parameter_78, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu: (-1x120x-1x-1xf32) <- (-1x120x-1x-1xf32)
        relu_9 = paddle._C_ops.relu(batch_norm__84)

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_5 = [1, 1]

        # pd_op.pool2d: (-1x120x1x1xf32) <- (-1x120x-1x-1xf32, 2xi64)
        pool2d_1 = paddle._C_ops.pool2d(relu_9, full_int_array_5, [1, 1], [0, 0], False, True, 'NCHW', 'avg', False, True, 'EXPLICIT')

        # pd_op.conv2d: (-1x30x1x1xf32) <- (-1x120x1x1xf32, 30x120x1x1xf32)
        conv2d_12 = paddle._C_ops.conv2d(pool2d_1, parameter_79, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_6 = [1, 30, 1, 1]

        # pd_op.reshape: (1x30x1x1xf32, 0x30xf32) <- (30xf32, 4xi64)
        reshape_4, reshape_5 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_80, full_int_array_6), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add_: (-1x30x1x1xf32) <- (-1x30x1x1xf32, 1x30x1x1xf32)
        add__2 = paddle._C_ops.add_(conv2d_12, reshape_4)

        # pd_op.relu_: (-1x30x1x1xf32) <- (-1x30x1x1xf32)
        relu__1 = paddle._C_ops.relu_(add__2)

        # pd_op.conv2d: (-1x120x1x1xf32) <- (-1x30x1x1xf32, 120x30x1x1xf32)
        conv2d_13 = paddle._C_ops.conv2d(relu__1, parameter_81, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_7 = [1, 120, 1, 1]

        # pd_op.reshape: (1x120x1x1xf32, 0x120xf32) <- (120xf32, 4xi64)
        reshape_6, reshape_7 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_82, full_int_array_7), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add_: (-1x120x1x1xf32) <- (-1x120x1x1xf32, 1x120x1x1xf32)
        add__3 = paddle._C_ops.add_(conv2d_13, reshape_6)

        # pd_op.hardsigmoid: (-1x120x1x1xf32) <- (-1x120x1x1xf32)
        hardsigmoid_1 = paddle._C_ops.hardsigmoid(add__3, float('0.2'), float('0.5'))

        # pd_op.multiply: (-1x120x-1x-1xf32) <- (-1x120x-1x-1xf32, -1x120x1x1xf32)
        multiply_1 = paddle._C_ops.multiply(relu_9, hardsigmoid_1)

        # pd_op.conv2d: (-1x40x-1x-1xf32) <- (-1x120x-1x-1xf32, 40x120x1x1xf32)
        conv2d_14 = paddle._C_ops.conv2d(multiply_1, parameter_83, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x40x-1x-1xf32, 40xf32, 40xf32, 40xf32, 40xf32, None) <- (-1x40x-1x-1xf32, 40xf32, 40xf32, 40xf32, 40xf32)
        batch_norm__90, batch_norm__91, batch_norm__92, batch_norm__93, batch_norm__94, batch_norm__95 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_14, parameter_84, parameter_85, parameter_86, parameter_87, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.add: (-1x40x-1x-1xf32) <- (-1x40x-1x-1xf32, -1x40x-1x-1xf32)
        add_2 = paddle._C_ops.add(batch_norm__72, batch_norm__90)

        # pd_op.conv2d: (-1x120x-1x-1xf32) <- (-1x40x-1x-1xf32, 120x40x1x1xf32)
        conv2d_15 = paddle._C_ops.conv2d(add_2, parameter_88, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x120x-1x-1xf32, 120xf32, 120xf32, 120xf32, 120xf32, None) <- (-1x120x-1x-1xf32, 120xf32, 120xf32, 120xf32, 120xf32)
        batch_norm__96, batch_norm__97, batch_norm__98, batch_norm__99, batch_norm__100, batch_norm__101 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_15, parameter_89, parameter_90, parameter_91, parameter_92, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu: (-1x120x-1x-1xf32) <- (-1x120x-1x-1xf32)
        relu_10 = paddle._C_ops.relu(batch_norm__96)

        # pd_op.depthwise_conv2d: (-1x120x-1x-1xf32) <- (-1x120x-1x-1xf32, 120x1x5x5xf32)
        depthwise_conv2d_5 = paddle._C_ops.depthwise_conv2d(relu_10, parameter_93, [1, 1], [2, 2], 'EXPLICIT', 120, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x120x-1x-1xf32, 120xf32, 120xf32, 120xf32, 120xf32, None) <- (-1x120x-1x-1xf32, 120xf32, 120xf32, 120xf32, 120xf32)
        batch_norm__102, batch_norm__103, batch_norm__104, batch_norm__105, batch_norm__106, batch_norm__107 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_5, parameter_94, parameter_95, parameter_96, parameter_97, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu: (-1x120x-1x-1xf32) <- (-1x120x-1x-1xf32)
        relu_11 = paddle._C_ops.relu(batch_norm__102)

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_8 = [1, 1]

        # pd_op.pool2d: (-1x120x1x1xf32) <- (-1x120x-1x-1xf32, 2xi64)
        pool2d_2 = paddle._C_ops.pool2d(relu_11, full_int_array_8, [1, 1], [0, 0], False, True, 'NCHW', 'avg', False, True, 'EXPLICIT')

        # pd_op.conv2d: (-1x30x1x1xf32) <- (-1x120x1x1xf32, 30x120x1x1xf32)
        conv2d_16 = paddle._C_ops.conv2d(pool2d_2, parameter_98, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_9 = [1, 30, 1, 1]

        # pd_op.reshape: (1x30x1x1xf32, 0x30xf32) <- (30xf32, 4xi64)
        reshape_8, reshape_9 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_99, full_int_array_9), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add_: (-1x30x1x1xf32) <- (-1x30x1x1xf32, 1x30x1x1xf32)
        add__4 = paddle._C_ops.add_(conv2d_16, reshape_8)

        # pd_op.relu_: (-1x30x1x1xf32) <- (-1x30x1x1xf32)
        relu__2 = paddle._C_ops.relu_(add__4)

        # pd_op.conv2d: (-1x120x1x1xf32) <- (-1x30x1x1xf32, 120x30x1x1xf32)
        conv2d_17 = paddle._C_ops.conv2d(relu__2, parameter_100, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_10 = [1, 120, 1, 1]

        # pd_op.reshape: (1x120x1x1xf32, 0x120xf32) <- (120xf32, 4xi64)
        reshape_10, reshape_11 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_101, full_int_array_10), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add_: (-1x120x1x1xf32) <- (-1x120x1x1xf32, 1x120x1x1xf32)
        add__5 = paddle._C_ops.add_(conv2d_17, reshape_10)

        # pd_op.hardsigmoid: (-1x120x1x1xf32) <- (-1x120x1x1xf32)
        hardsigmoid_2 = paddle._C_ops.hardsigmoid(add__5, float('0.2'), float('0.5'))

        # pd_op.multiply: (-1x120x-1x-1xf32) <- (-1x120x-1x-1xf32, -1x120x1x1xf32)
        multiply_2 = paddle._C_ops.multiply(relu_11, hardsigmoid_2)

        # pd_op.conv2d: (-1x40x-1x-1xf32) <- (-1x120x-1x-1xf32, 40x120x1x1xf32)
        conv2d_18 = paddle._C_ops.conv2d(multiply_2, parameter_102, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x40x-1x-1xf32, 40xf32, 40xf32, 40xf32, 40xf32, None) <- (-1x40x-1x-1xf32, 40xf32, 40xf32, 40xf32, 40xf32)
        batch_norm__108, batch_norm__109, batch_norm__110, batch_norm__111, batch_norm__112, batch_norm__113 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_18, parameter_103, parameter_104, parameter_105, parameter_106, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.add: (-1x40x-1x-1xf32) <- (-1x40x-1x-1xf32, -1x40x-1x-1xf32)
        add_3 = paddle._C_ops.add(add_2, batch_norm__108)

        # pd_op.conv2d: (-1x240x-1x-1xf32) <- (-1x40x-1x-1xf32, 240x40x1x1xf32)
        conv2d_19 = paddle._C_ops.conv2d(add_3, parameter_107, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x240x-1x-1xf32, 240xf32, 240xf32, 240xf32, 240xf32, None) <- (-1x240x-1x-1xf32, 240xf32, 240xf32, 240xf32, 240xf32)
        batch_norm__114, batch_norm__115, batch_norm__116, batch_norm__117, batch_norm__118, batch_norm__119 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_19, parameter_108, parameter_109, parameter_110, parameter_111, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x240x-1x-1xf32) <- (-1x240x-1x-1xf32)
        hardswish_1 = paddle._C_ops.hardswish(batch_norm__114)

        # pd_op.depthwise_conv2d: (-1x240x-1x-1xf32) <- (-1x240x-1x-1xf32, 240x1x3x3xf32)
        depthwise_conv2d_6 = paddle._C_ops.depthwise_conv2d(hardswish_1, parameter_112, [2, 2], [1, 1], 'EXPLICIT', 240, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x240x-1x-1xf32, 240xf32, 240xf32, 240xf32, 240xf32, None) <- (-1x240x-1x-1xf32, 240xf32, 240xf32, 240xf32, 240xf32)
        batch_norm__120, batch_norm__121, batch_norm__122, batch_norm__123, batch_norm__124, batch_norm__125 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_6, parameter_113, parameter_114, parameter_115, parameter_116, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x240x-1x-1xf32) <- (-1x240x-1x-1xf32)
        hardswish_2 = paddle._C_ops.hardswish(batch_norm__120)

        # pd_op.conv2d: (-1x80x-1x-1xf32) <- (-1x240x-1x-1xf32, 80x240x1x1xf32)
        conv2d_20 = paddle._C_ops.conv2d(hardswish_2, parameter_117, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x80x-1x-1xf32, 80xf32, 80xf32, 80xf32, 80xf32, None) <- (-1x80x-1x-1xf32, 80xf32, 80xf32, 80xf32, 80xf32)
        batch_norm__126, batch_norm__127, batch_norm__128, batch_norm__129, batch_norm__130, batch_norm__131 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_20, parameter_118, parameter_119, parameter_120, parameter_121, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x200x-1x-1xf32) <- (-1x80x-1x-1xf32, 200x80x1x1xf32)
        conv2d_21 = paddle._C_ops.conv2d(batch_norm__126, parameter_122, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x200x-1x-1xf32, 200xf32, 200xf32, 200xf32, 200xf32, None) <- (-1x200x-1x-1xf32, 200xf32, 200xf32, 200xf32, 200xf32)
        batch_norm__132, batch_norm__133, batch_norm__134, batch_norm__135, batch_norm__136, batch_norm__137 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_21, parameter_123, parameter_124, parameter_125, parameter_126, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x200x-1x-1xf32) <- (-1x200x-1x-1xf32)
        hardswish_3 = paddle._C_ops.hardswish(batch_norm__132)

        # pd_op.depthwise_conv2d: (-1x200x-1x-1xf32) <- (-1x200x-1x-1xf32, 200x1x3x3xf32)
        depthwise_conv2d_7 = paddle._C_ops.depthwise_conv2d(hardswish_3, parameter_127, [1, 1], [1, 1], 'EXPLICIT', 200, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x200x-1x-1xf32, 200xf32, 200xf32, 200xf32, 200xf32, None) <- (-1x200x-1x-1xf32, 200xf32, 200xf32, 200xf32, 200xf32)
        batch_norm__138, batch_norm__139, batch_norm__140, batch_norm__141, batch_norm__142, batch_norm__143 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_7, parameter_128, parameter_129, parameter_130, parameter_131, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x200x-1x-1xf32) <- (-1x200x-1x-1xf32)
        hardswish_4 = paddle._C_ops.hardswish(batch_norm__138)

        # pd_op.conv2d: (-1x80x-1x-1xf32) <- (-1x200x-1x-1xf32, 80x200x1x1xf32)
        conv2d_22 = paddle._C_ops.conv2d(hardswish_4, parameter_132, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x80x-1x-1xf32, 80xf32, 80xf32, 80xf32, 80xf32, None) <- (-1x80x-1x-1xf32, 80xf32, 80xf32, 80xf32, 80xf32)
        batch_norm__144, batch_norm__145, batch_norm__146, batch_norm__147, batch_norm__148, batch_norm__149 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_22, parameter_133, parameter_134, parameter_135, parameter_136, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.add: (-1x80x-1x-1xf32) <- (-1x80x-1x-1xf32, -1x80x-1x-1xf32)
        add_4 = paddle._C_ops.add(batch_norm__126, batch_norm__144)

        # pd_op.conv2d: (-1x184x-1x-1xf32) <- (-1x80x-1x-1xf32, 184x80x1x1xf32)
        conv2d_23 = paddle._C_ops.conv2d(add_4, parameter_137, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x184x-1x-1xf32, 184xf32, 184xf32, 184xf32, 184xf32, None) <- (-1x184x-1x-1xf32, 184xf32, 184xf32, 184xf32, 184xf32)
        batch_norm__150, batch_norm__151, batch_norm__152, batch_norm__153, batch_norm__154, batch_norm__155 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_23, parameter_138, parameter_139, parameter_140, parameter_141, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x184x-1x-1xf32) <- (-1x184x-1x-1xf32)
        hardswish_5 = paddle._C_ops.hardswish(batch_norm__150)

        # pd_op.depthwise_conv2d: (-1x184x-1x-1xf32) <- (-1x184x-1x-1xf32, 184x1x3x3xf32)
        depthwise_conv2d_8 = paddle._C_ops.depthwise_conv2d(hardswish_5, parameter_142, [1, 1], [1, 1], 'EXPLICIT', 184, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x184x-1x-1xf32, 184xf32, 184xf32, 184xf32, 184xf32, None) <- (-1x184x-1x-1xf32, 184xf32, 184xf32, 184xf32, 184xf32)
        batch_norm__156, batch_norm__157, batch_norm__158, batch_norm__159, batch_norm__160, batch_norm__161 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_8, parameter_143, parameter_144, parameter_145, parameter_146, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x184x-1x-1xf32) <- (-1x184x-1x-1xf32)
        hardswish_6 = paddle._C_ops.hardswish(batch_norm__156)

        # pd_op.conv2d: (-1x80x-1x-1xf32) <- (-1x184x-1x-1xf32, 80x184x1x1xf32)
        conv2d_24 = paddle._C_ops.conv2d(hardswish_6, parameter_147, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x80x-1x-1xf32, 80xf32, 80xf32, 80xf32, 80xf32, None) <- (-1x80x-1x-1xf32, 80xf32, 80xf32, 80xf32, 80xf32)
        batch_norm__162, batch_norm__163, batch_norm__164, batch_norm__165, batch_norm__166, batch_norm__167 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_24, parameter_148, parameter_149, parameter_150, parameter_151, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.add: (-1x80x-1x-1xf32) <- (-1x80x-1x-1xf32, -1x80x-1x-1xf32)
        add_5 = paddle._C_ops.add(add_4, batch_norm__162)

        # pd_op.conv2d: (-1x184x-1x-1xf32) <- (-1x80x-1x-1xf32, 184x80x1x1xf32)
        conv2d_25 = paddle._C_ops.conv2d(add_5, parameter_152, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x184x-1x-1xf32, 184xf32, 184xf32, 184xf32, 184xf32, None) <- (-1x184x-1x-1xf32, 184xf32, 184xf32, 184xf32, 184xf32)
        batch_norm__168, batch_norm__169, batch_norm__170, batch_norm__171, batch_norm__172, batch_norm__173 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_25, parameter_153, parameter_154, parameter_155, parameter_156, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x184x-1x-1xf32) <- (-1x184x-1x-1xf32)
        hardswish_7 = paddle._C_ops.hardswish(batch_norm__168)

        # pd_op.depthwise_conv2d: (-1x184x-1x-1xf32) <- (-1x184x-1x-1xf32, 184x1x3x3xf32)
        depthwise_conv2d_9 = paddle._C_ops.depthwise_conv2d(hardswish_7, parameter_157, [1, 1], [1, 1], 'EXPLICIT', 184, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x184x-1x-1xf32, 184xf32, 184xf32, 184xf32, 184xf32, None) <- (-1x184x-1x-1xf32, 184xf32, 184xf32, 184xf32, 184xf32)
        batch_norm__174, batch_norm__175, batch_norm__176, batch_norm__177, batch_norm__178, batch_norm__179 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_9, parameter_158, parameter_159, parameter_160, parameter_161, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x184x-1x-1xf32) <- (-1x184x-1x-1xf32)
        hardswish_8 = paddle._C_ops.hardswish(batch_norm__174)

        # pd_op.conv2d: (-1x80x-1x-1xf32) <- (-1x184x-1x-1xf32, 80x184x1x1xf32)
        conv2d_26 = paddle._C_ops.conv2d(hardswish_8, parameter_162, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x80x-1x-1xf32, 80xf32, 80xf32, 80xf32, 80xf32, None) <- (-1x80x-1x-1xf32, 80xf32, 80xf32, 80xf32, 80xf32)
        batch_norm__180, batch_norm__181, batch_norm__182, batch_norm__183, batch_norm__184, batch_norm__185 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_26, parameter_163, parameter_164, parameter_165, parameter_166, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.add: (-1x80x-1x-1xf32) <- (-1x80x-1x-1xf32, -1x80x-1x-1xf32)
        add_6 = paddle._C_ops.add(add_5, batch_norm__180)

        # pd_op.conv2d: (-1x480x-1x-1xf32) <- (-1x80x-1x-1xf32, 480x80x1x1xf32)
        conv2d_27 = paddle._C_ops.conv2d(add_6, parameter_167, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x480x-1x-1xf32, 480xf32, 480xf32, 480xf32, 480xf32, None) <- (-1x480x-1x-1xf32, 480xf32, 480xf32, 480xf32, 480xf32)
        batch_norm__186, batch_norm__187, batch_norm__188, batch_norm__189, batch_norm__190, batch_norm__191 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_27, parameter_168, parameter_169, parameter_170, parameter_171, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x480x-1x-1xf32) <- (-1x480x-1x-1xf32)
        hardswish_9 = paddle._C_ops.hardswish(batch_norm__186)

        # pd_op.depthwise_conv2d: (-1x480x-1x-1xf32) <- (-1x480x-1x-1xf32, 480x1x3x3xf32)
        depthwise_conv2d_10 = paddle._C_ops.depthwise_conv2d(hardswish_9, parameter_172, [1, 1], [1, 1], 'EXPLICIT', 480, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x480x-1x-1xf32, 480xf32, 480xf32, 480xf32, 480xf32, None) <- (-1x480x-1x-1xf32, 480xf32, 480xf32, 480xf32, 480xf32)
        batch_norm__192, batch_norm__193, batch_norm__194, batch_norm__195, batch_norm__196, batch_norm__197 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_10, parameter_173, parameter_174, parameter_175, parameter_176, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x480x-1x-1xf32) <- (-1x480x-1x-1xf32)
        hardswish_10 = paddle._C_ops.hardswish(batch_norm__192)

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_11 = [1, 1]

        # pd_op.pool2d: (-1x480x1x1xf32) <- (-1x480x-1x-1xf32, 2xi64)
        pool2d_3 = paddle._C_ops.pool2d(hardswish_10, full_int_array_11, [1, 1], [0, 0], False, True, 'NCHW', 'avg', False, True, 'EXPLICIT')

        # pd_op.conv2d: (-1x120x1x1xf32) <- (-1x480x1x1xf32, 120x480x1x1xf32)
        conv2d_28 = paddle._C_ops.conv2d(pool2d_3, parameter_177, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_12 = [1, 120, 1, 1]

        # pd_op.reshape: (1x120x1x1xf32, 0x120xf32) <- (120xf32, 4xi64)
        reshape_12, reshape_13 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_178, full_int_array_12), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add_: (-1x120x1x1xf32) <- (-1x120x1x1xf32, 1x120x1x1xf32)
        add__6 = paddle._C_ops.add_(conv2d_28, reshape_12)

        # pd_op.relu_: (-1x120x1x1xf32) <- (-1x120x1x1xf32)
        relu__3 = paddle._C_ops.relu_(add__6)

        # pd_op.conv2d: (-1x480x1x1xf32) <- (-1x120x1x1xf32, 480x120x1x1xf32)
        conv2d_29 = paddle._C_ops.conv2d(relu__3, parameter_179, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_13 = [1, 480, 1, 1]

        # pd_op.reshape: (1x480x1x1xf32, 0x480xf32) <- (480xf32, 4xi64)
        reshape_14, reshape_15 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_180, full_int_array_13), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add_: (-1x480x1x1xf32) <- (-1x480x1x1xf32, 1x480x1x1xf32)
        add__7 = paddle._C_ops.add_(conv2d_29, reshape_14)

        # pd_op.hardsigmoid: (-1x480x1x1xf32) <- (-1x480x1x1xf32)
        hardsigmoid_3 = paddle._C_ops.hardsigmoid(add__7, float('0.2'), float('0.5'))

        # pd_op.multiply: (-1x480x-1x-1xf32) <- (-1x480x-1x-1xf32, -1x480x1x1xf32)
        multiply_3 = paddle._C_ops.multiply(hardswish_10, hardsigmoid_3)

        # pd_op.conv2d: (-1x112x-1x-1xf32) <- (-1x480x-1x-1xf32, 112x480x1x1xf32)
        conv2d_30 = paddle._C_ops.conv2d(multiply_3, parameter_181, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x112x-1x-1xf32, 112xf32, 112xf32, 112xf32, 112xf32, None) <- (-1x112x-1x-1xf32, 112xf32, 112xf32, 112xf32, 112xf32)
        batch_norm__198, batch_norm__199, batch_norm__200, batch_norm__201, batch_norm__202, batch_norm__203 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_30, parameter_182, parameter_183, parameter_184, parameter_185, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x672x-1x-1xf32) <- (-1x112x-1x-1xf32, 672x112x1x1xf32)
        conv2d_31 = paddle._C_ops.conv2d(batch_norm__198, parameter_186, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x672x-1x-1xf32, 672xf32, 672xf32, 672xf32, 672xf32, None) <- (-1x672x-1x-1xf32, 672xf32, 672xf32, 672xf32, 672xf32)
        batch_norm__204, batch_norm__205, batch_norm__206, batch_norm__207, batch_norm__208, batch_norm__209 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_31, parameter_187, parameter_188, parameter_189, parameter_190, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x672x-1x-1xf32) <- (-1x672x-1x-1xf32)
        hardswish_11 = paddle._C_ops.hardswish(batch_norm__204)

        # pd_op.depthwise_conv2d: (-1x672x-1x-1xf32) <- (-1x672x-1x-1xf32, 672x1x3x3xf32)
        depthwise_conv2d_11 = paddle._C_ops.depthwise_conv2d(hardswish_11, parameter_191, [1, 1], [1, 1], 'EXPLICIT', 672, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x672x-1x-1xf32, 672xf32, 672xf32, 672xf32, 672xf32, None) <- (-1x672x-1x-1xf32, 672xf32, 672xf32, 672xf32, 672xf32)
        batch_norm__210, batch_norm__211, batch_norm__212, batch_norm__213, batch_norm__214, batch_norm__215 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_11, parameter_192, parameter_193, parameter_194, parameter_195, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x672x-1x-1xf32) <- (-1x672x-1x-1xf32)
        hardswish_12 = paddle._C_ops.hardswish(batch_norm__210)

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_14 = [1, 1]

        # pd_op.pool2d: (-1x672x1x1xf32) <- (-1x672x-1x-1xf32, 2xi64)
        pool2d_4 = paddle._C_ops.pool2d(hardswish_12, full_int_array_14, [1, 1], [0, 0], False, True, 'NCHW', 'avg', False, True, 'EXPLICIT')

        # pd_op.conv2d: (-1x168x1x1xf32) <- (-1x672x1x1xf32, 168x672x1x1xf32)
        conv2d_32 = paddle._C_ops.conv2d(pool2d_4, parameter_196, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_15 = [1, 168, 1, 1]

        # pd_op.reshape: (1x168x1x1xf32, 0x168xf32) <- (168xf32, 4xi64)
        reshape_16, reshape_17 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_197, full_int_array_15), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add_: (-1x168x1x1xf32) <- (-1x168x1x1xf32, 1x168x1x1xf32)
        add__8 = paddle._C_ops.add_(conv2d_32, reshape_16)

        # pd_op.relu_: (-1x168x1x1xf32) <- (-1x168x1x1xf32)
        relu__4 = paddle._C_ops.relu_(add__8)

        # pd_op.conv2d: (-1x672x1x1xf32) <- (-1x168x1x1xf32, 672x168x1x1xf32)
        conv2d_33 = paddle._C_ops.conv2d(relu__4, parameter_198, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_16 = [1, 672, 1, 1]

        # pd_op.reshape: (1x672x1x1xf32, 0x672xf32) <- (672xf32, 4xi64)
        reshape_18, reshape_19 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_199, full_int_array_16), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add_: (-1x672x1x1xf32) <- (-1x672x1x1xf32, 1x672x1x1xf32)
        add__9 = paddle._C_ops.add_(conv2d_33, reshape_18)

        # pd_op.hardsigmoid: (-1x672x1x1xf32) <- (-1x672x1x1xf32)
        hardsigmoid_4 = paddle._C_ops.hardsigmoid(add__9, float('0.2'), float('0.5'))

        # pd_op.multiply: (-1x672x-1x-1xf32) <- (-1x672x-1x-1xf32, -1x672x1x1xf32)
        multiply_4 = paddle._C_ops.multiply(hardswish_12, hardsigmoid_4)

        # pd_op.conv2d: (-1x112x-1x-1xf32) <- (-1x672x-1x-1xf32, 112x672x1x1xf32)
        conv2d_34 = paddle._C_ops.conv2d(multiply_4, parameter_200, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x112x-1x-1xf32, 112xf32, 112xf32, 112xf32, 112xf32, None) <- (-1x112x-1x-1xf32, 112xf32, 112xf32, 112xf32, 112xf32)
        batch_norm__216, batch_norm__217, batch_norm__218, batch_norm__219, batch_norm__220, batch_norm__221 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_34, parameter_201, parameter_202, parameter_203, parameter_204, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.add: (-1x112x-1x-1xf32) <- (-1x112x-1x-1xf32, -1x112x-1x-1xf32)
        add_7 = paddle._C_ops.add(batch_norm__198, batch_norm__216)

        # pd_op.conv2d: (-1x672x-1x-1xf32) <- (-1x112x-1x-1xf32, 672x112x1x1xf32)
        conv2d_35 = paddle._C_ops.conv2d(add_7, parameter_205, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x672x-1x-1xf32, 672xf32, 672xf32, 672xf32, 672xf32, None) <- (-1x672x-1x-1xf32, 672xf32, 672xf32, 672xf32, 672xf32)
        batch_norm__222, batch_norm__223, batch_norm__224, batch_norm__225, batch_norm__226, batch_norm__227 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_35, parameter_206, parameter_207, parameter_208, parameter_209, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x672x-1x-1xf32) <- (-1x672x-1x-1xf32)
        hardswish_13 = paddle._C_ops.hardswish(batch_norm__222)

        # pd_op.depthwise_conv2d: (-1x672x-1x-1xf32) <- (-1x672x-1x-1xf32, 672x1x5x5xf32)
        depthwise_conv2d_12 = paddle._C_ops.depthwise_conv2d(hardswish_13, parameter_210, [2, 2], [2, 2], 'EXPLICIT', 672, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x672x-1x-1xf32, 672xf32, 672xf32, 672xf32, 672xf32, None) <- (-1x672x-1x-1xf32, 672xf32, 672xf32, 672xf32, 672xf32)
        batch_norm__228, batch_norm__229, batch_norm__230, batch_norm__231, batch_norm__232, batch_norm__233 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_12, parameter_211, parameter_212, parameter_213, parameter_214, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x672x-1x-1xf32) <- (-1x672x-1x-1xf32)
        hardswish_14 = paddle._C_ops.hardswish(batch_norm__228)

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_17 = [1, 1]

        # pd_op.pool2d: (-1x672x1x1xf32) <- (-1x672x-1x-1xf32, 2xi64)
        pool2d_5 = paddle._C_ops.pool2d(hardswish_14, full_int_array_17, [1, 1], [0, 0], False, True, 'NCHW', 'avg', False, True, 'EXPLICIT')

        # pd_op.conv2d: (-1x168x1x1xf32) <- (-1x672x1x1xf32, 168x672x1x1xf32)
        conv2d_36 = paddle._C_ops.conv2d(pool2d_5, parameter_215, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_18 = [1, 168, 1, 1]

        # pd_op.reshape: (1x168x1x1xf32, 0x168xf32) <- (168xf32, 4xi64)
        reshape_20, reshape_21 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_216, full_int_array_18), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add_: (-1x168x1x1xf32) <- (-1x168x1x1xf32, 1x168x1x1xf32)
        add__10 = paddle._C_ops.add_(conv2d_36, reshape_20)

        # pd_op.relu_: (-1x168x1x1xf32) <- (-1x168x1x1xf32)
        relu__5 = paddle._C_ops.relu_(add__10)

        # pd_op.conv2d: (-1x672x1x1xf32) <- (-1x168x1x1xf32, 672x168x1x1xf32)
        conv2d_37 = paddle._C_ops.conv2d(relu__5, parameter_217, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_19 = [1, 672, 1, 1]

        # pd_op.reshape: (1x672x1x1xf32, 0x672xf32) <- (672xf32, 4xi64)
        reshape_22, reshape_23 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_218, full_int_array_19), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add_: (-1x672x1x1xf32) <- (-1x672x1x1xf32, 1x672x1x1xf32)
        add__11 = paddle._C_ops.add_(conv2d_37, reshape_22)

        # pd_op.hardsigmoid: (-1x672x1x1xf32) <- (-1x672x1x1xf32)
        hardsigmoid_5 = paddle._C_ops.hardsigmoid(add__11, float('0.2'), float('0.5'))

        # pd_op.multiply: (-1x672x-1x-1xf32) <- (-1x672x-1x-1xf32, -1x672x1x1xf32)
        multiply_5 = paddle._C_ops.multiply(hardswish_14, hardsigmoid_5)

        # pd_op.conv2d: (-1x160x-1x-1xf32) <- (-1x672x-1x-1xf32, 160x672x1x1xf32)
        conv2d_38 = paddle._C_ops.conv2d(multiply_5, parameter_219, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x160x-1x-1xf32, 160xf32, 160xf32, 160xf32, 160xf32, None) <- (-1x160x-1x-1xf32, 160xf32, 160xf32, 160xf32, 160xf32)
        batch_norm__234, batch_norm__235, batch_norm__236, batch_norm__237, batch_norm__238, batch_norm__239 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_38, parameter_220, parameter_221, parameter_222, parameter_223, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x960x-1x-1xf32) <- (-1x160x-1x-1xf32, 960x160x1x1xf32)
        conv2d_39 = paddle._C_ops.conv2d(batch_norm__234, parameter_224, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x960x-1x-1xf32, 960xf32, 960xf32, 960xf32, 960xf32, None) <- (-1x960x-1x-1xf32, 960xf32, 960xf32, 960xf32, 960xf32)
        batch_norm__240, batch_norm__241, batch_norm__242, batch_norm__243, batch_norm__244, batch_norm__245 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_39, parameter_225, parameter_226, parameter_227, parameter_228, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x960x-1x-1xf32) <- (-1x960x-1x-1xf32)
        hardswish_15 = paddle._C_ops.hardswish(batch_norm__240)

        # pd_op.depthwise_conv2d: (-1x960x-1x-1xf32) <- (-1x960x-1x-1xf32, 960x1x5x5xf32)
        depthwise_conv2d_13 = paddle._C_ops.depthwise_conv2d(hardswish_15, parameter_229, [1, 1], [2, 2], 'EXPLICIT', 960, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x960x-1x-1xf32, 960xf32, 960xf32, 960xf32, 960xf32, None) <- (-1x960x-1x-1xf32, 960xf32, 960xf32, 960xf32, 960xf32)
        batch_norm__246, batch_norm__247, batch_norm__248, batch_norm__249, batch_norm__250, batch_norm__251 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_13, parameter_230, parameter_231, parameter_232, parameter_233, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x960x-1x-1xf32) <- (-1x960x-1x-1xf32)
        hardswish_16 = paddle._C_ops.hardswish(batch_norm__246)

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_20 = [1, 1]

        # pd_op.pool2d: (-1x960x1x1xf32) <- (-1x960x-1x-1xf32, 2xi64)
        pool2d_6 = paddle._C_ops.pool2d(hardswish_16, full_int_array_20, [1, 1], [0, 0], False, True, 'NCHW', 'avg', False, True, 'EXPLICIT')

        # pd_op.conv2d: (-1x240x1x1xf32) <- (-1x960x1x1xf32, 240x960x1x1xf32)
        conv2d_40 = paddle._C_ops.conv2d(pool2d_6, parameter_234, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_21 = [1, 240, 1, 1]

        # pd_op.reshape: (1x240x1x1xf32, 0x240xf32) <- (240xf32, 4xi64)
        reshape_24, reshape_25 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_235, full_int_array_21), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add_: (-1x240x1x1xf32) <- (-1x240x1x1xf32, 1x240x1x1xf32)
        add__12 = paddle._C_ops.add_(conv2d_40, reshape_24)

        # pd_op.relu_: (-1x240x1x1xf32) <- (-1x240x1x1xf32)
        relu__6 = paddle._C_ops.relu_(add__12)

        # pd_op.conv2d: (-1x960x1x1xf32) <- (-1x240x1x1xf32, 960x240x1x1xf32)
        conv2d_41 = paddle._C_ops.conv2d(relu__6, parameter_236, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_22 = [1, 960, 1, 1]

        # pd_op.reshape: (1x960x1x1xf32, 0x960xf32) <- (960xf32, 4xi64)
        reshape_26, reshape_27 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_237, full_int_array_22), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add_: (-1x960x1x1xf32) <- (-1x960x1x1xf32, 1x960x1x1xf32)
        add__13 = paddle._C_ops.add_(conv2d_41, reshape_26)

        # pd_op.hardsigmoid: (-1x960x1x1xf32) <- (-1x960x1x1xf32)
        hardsigmoid_6 = paddle._C_ops.hardsigmoid(add__13, float('0.2'), float('0.5'))

        # pd_op.multiply: (-1x960x-1x-1xf32) <- (-1x960x-1x-1xf32, -1x960x1x1xf32)
        multiply_6 = paddle._C_ops.multiply(hardswish_16, hardsigmoid_6)

        # pd_op.conv2d: (-1x160x-1x-1xf32) <- (-1x960x-1x-1xf32, 160x960x1x1xf32)
        conv2d_42 = paddle._C_ops.conv2d(multiply_6, parameter_238, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x160x-1x-1xf32, 160xf32, 160xf32, 160xf32, 160xf32, None) <- (-1x160x-1x-1xf32, 160xf32, 160xf32, 160xf32, 160xf32)
        batch_norm__252, batch_norm__253, batch_norm__254, batch_norm__255, batch_norm__256, batch_norm__257 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_42, parameter_239, parameter_240, parameter_241, parameter_242, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.add: (-1x160x-1x-1xf32) <- (-1x160x-1x-1xf32, -1x160x-1x-1xf32)
        add_8 = paddle._C_ops.add(batch_norm__234, batch_norm__252)

        # pd_op.conv2d: (-1x960x-1x-1xf32) <- (-1x160x-1x-1xf32, 960x160x1x1xf32)
        conv2d_43 = paddle._C_ops.conv2d(add_8, parameter_243, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x960x-1x-1xf32, 960xf32, 960xf32, 960xf32, 960xf32, None) <- (-1x960x-1x-1xf32, 960xf32, 960xf32, 960xf32, 960xf32)
        batch_norm__258, batch_norm__259, batch_norm__260, batch_norm__261, batch_norm__262, batch_norm__263 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_43, parameter_244, parameter_245, parameter_246, parameter_247, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x960x-1x-1xf32) <- (-1x960x-1x-1xf32)
        hardswish_17 = paddle._C_ops.hardswish(batch_norm__258)

        # pd_op.depthwise_conv2d: (-1x960x-1x-1xf32) <- (-1x960x-1x-1xf32, 960x1x5x5xf32)
        depthwise_conv2d_14 = paddle._C_ops.depthwise_conv2d(hardswish_17, parameter_248, [1, 1], [2, 2], 'EXPLICIT', 960, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x960x-1x-1xf32, 960xf32, 960xf32, 960xf32, 960xf32, None) <- (-1x960x-1x-1xf32, 960xf32, 960xf32, 960xf32, 960xf32)
        batch_norm__264, batch_norm__265, batch_norm__266, batch_norm__267, batch_norm__268, batch_norm__269 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_14, parameter_249, parameter_250, parameter_251, parameter_252, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x960x-1x-1xf32) <- (-1x960x-1x-1xf32)
        hardswish_18 = paddle._C_ops.hardswish(batch_norm__264)

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_23 = [1, 1]

        # pd_op.pool2d: (-1x960x1x1xf32) <- (-1x960x-1x-1xf32, 2xi64)
        pool2d_7 = paddle._C_ops.pool2d(hardswish_18, full_int_array_23, [1, 1], [0, 0], False, True, 'NCHW', 'avg', False, True, 'EXPLICIT')

        # pd_op.conv2d: (-1x240x1x1xf32) <- (-1x960x1x1xf32, 240x960x1x1xf32)
        conv2d_44 = paddle._C_ops.conv2d(pool2d_7, parameter_253, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_24 = [1, 240, 1, 1]

        # pd_op.reshape: (1x240x1x1xf32, 0x240xf32) <- (240xf32, 4xi64)
        reshape_28, reshape_29 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_254, full_int_array_24), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add_: (-1x240x1x1xf32) <- (-1x240x1x1xf32, 1x240x1x1xf32)
        add__14 = paddle._C_ops.add_(conv2d_44, reshape_28)

        # pd_op.relu_: (-1x240x1x1xf32) <- (-1x240x1x1xf32)
        relu__7 = paddle._C_ops.relu_(add__14)

        # pd_op.conv2d: (-1x960x1x1xf32) <- (-1x240x1x1xf32, 960x240x1x1xf32)
        conv2d_45 = paddle._C_ops.conv2d(relu__7, parameter_255, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_25 = [1, 960, 1, 1]

        # pd_op.reshape: (1x960x1x1xf32, 0x960xf32) <- (960xf32, 4xi64)
        reshape_30, reshape_31 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_256, full_int_array_25), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add_: (-1x960x1x1xf32) <- (-1x960x1x1xf32, 1x960x1x1xf32)
        add__15 = paddle._C_ops.add_(conv2d_45, reshape_30)

        # pd_op.hardsigmoid: (-1x960x1x1xf32) <- (-1x960x1x1xf32)
        hardsigmoid_7 = paddle._C_ops.hardsigmoid(add__15, float('0.2'), float('0.5'))

        # pd_op.multiply: (-1x960x-1x-1xf32) <- (-1x960x-1x-1xf32, -1x960x1x1xf32)
        multiply_7 = paddle._C_ops.multiply(hardswish_18, hardsigmoid_7)

        # pd_op.conv2d: (-1x160x-1x-1xf32) <- (-1x960x-1x-1xf32, 160x960x1x1xf32)
        conv2d_46 = paddle._C_ops.conv2d(multiply_7, parameter_257, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x160x-1x-1xf32, 160xf32, 160xf32, 160xf32, 160xf32, None) <- (-1x160x-1x-1xf32, 160xf32, 160xf32, 160xf32, 160xf32)
        batch_norm__270, batch_norm__271, batch_norm__272, batch_norm__273, batch_norm__274, batch_norm__275 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_46, parameter_258, parameter_259, parameter_260, parameter_261, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.add: (-1x160x-1x-1xf32) <- (-1x160x-1x-1xf32, -1x160x-1x-1xf32)
        add_9 = paddle._C_ops.add(add_8, batch_norm__270)

        # pd_op.shape: (4xi32) <- (-1x160x-1x-1xf32)
        shape_1 = paddle._C_ops.shape(add_9)

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_26 = [2]

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_27 = [2147483647]

        # pd_op.slice: (2xi32) <- (4xi32, 1xi64, 1xi64)
        slice_1 = paddle._C_ops.slice(shape_1, [0], full_int_array_26, full_int_array_27, [1], [])

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_28 = [1, 1]

        # pd_op.pool2d: (-1x160x1x1xf32) <- (-1x160x-1x-1xf32, 2xi64)
        pool2d_8 = paddle._C_ops.pool2d(add_9, full_int_array_28, [1, 1], [0, 0], False, True, 'NCHW', 'avg', False, True, 'EXPLICIT')

        # pd_op.conv2d: (-1x128x1x1xf32) <- (-1x160x1x1xf32, 128x160x1x1xf32)
        conv2d_47 = paddle._C_ops.conv2d(pool2d_8, parameter_262, [1, 1], [0, 0], 'SAME', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_29 = [1, 128, 1, 1]

        # pd_op.reshape: (1x128x1x1xf32, 0x128xf32) <- (128xf32, 4xi64)
        reshape_32, reshape_33 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_263, full_int_array_29), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add_: (-1x128x1x1xf32) <- (-1x128x1x1xf32, 1x128x1x1xf32)
        add__16 = paddle._C_ops.add_(conv2d_47, reshape_32)

        # pd_op.batch_norm_: (-1x128x1x1xf32, 128xf32, 128xf32, xf32, xf32, None) <- (-1x128x1x1xf32, 128xf32, 128xf32, 128xf32, 128xf32)
        batch_norm__276, batch_norm__277, batch_norm__278, batch_norm__279, batch_norm__280, batch_norm__281 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(add__16, parameter_264, parameter_265, parameter_266, parameter_267, True, float('0.9'), float('1e-05'), 'NCHW', True, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x128x1x1xf32) <- (-1x128x1x1xf32)
        relu__8 = paddle._C_ops.relu_(batch_norm__276)

        # pd_op.cast: (2xi32) <- (2xi32)
        cast_0 = paddle._C_ops.cast(slice_1, paddle.int32)

        # pd_op.bilinear_interp: (-1x128x-1x-1xf32) <- (-1x128x1x1xf32, 2xi32, None, None)
        bilinear_interp_0 = paddle._C_ops.bilinear_interp(relu__8, cast_0, None, None, 'NCHW', -1, -1, -1, [], 'bilinear', False, 0)

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_30 = [2, 2]

        # pd_op.pool2d: (-1x160x2x2xf32) <- (-1x160x-1x-1xf32, 2xi64)
        pool2d_9 = paddle._C_ops.pool2d(add_9, full_int_array_30, [1, 1], [0, 0], False, True, 'NCHW', 'avg', False, True, 'EXPLICIT')

        # pd_op.conv2d: (-1x128x2x2xf32) <- (-1x160x2x2xf32, 128x160x1x1xf32)
        conv2d_48 = paddle._C_ops.conv2d(pool2d_9, parameter_268, [1, 1], [0, 0], 'SAME', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_31 = [1, 128, 1, 1]

        # pd_op.reshape: (1x128x1x1xf32, 0x128xf32) <- (128xf32, 4xi64)
        reshape_34, reshape_35 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_269, full_int_array_31), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add_: (-1x128x2x2xf32) <- (-1x128x2x2xf32, 1x128x1x1xf32)
        add__17 = paddle._C_ops.add_(conv2d_48, reshape_34)

        # pd_op.batch_norm_: (-1x128x2x2xf32, 128xf32, 128xf32, xf32, xf32, None) <- (-1x128x2x2xf32, 128xf32, 128xf32, 128xf32, 128xf32)
        batch_norm__282, batch_norm__283, batch_norm__284, batch_norm__285, batch_norm__286, batch_norm__287 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(add__17, parameter_270, parameter_271, parameter_272, parameter_273, True, float('0.9'), float('1e-05'), 'NCHW', True, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x128x2x2xf32) <- (-1x128x2x2xf32)
        relu__9 = paddle._C_ops.relu_(batch_norm__282)

        # pd_op.cast: (2xi32) <- (2xi32)
        cast_1 = paddle._C_ops.cast(slice_1, paddle.int32)

        # pd_op.bilinear_interp: (-1x128x-1x-1xf32) <- (-1x128x2x2xf32, 2xi32, None, None)
        bilinear_interp_1 = paddle._C_ops.bilinear_interp(relu__9, cast_1, None, None, 'NCHW', -1, -1, -1, [], 'bilinear', False, 0)

        # pd_op.add: (-1x128x-1x-1xf32) <- (-1x128x-1x-1xf32, -1x128x-1x-1xf32)
        add_10 = paddle._C_ops.add(bilinear_interp_0, bilinear_interp_1)

        # pd_op.full_int_array: (2xi64) <- ()
        full_int_array_32 = [4, 4]

        # pd_op.pool2d: (-1x160x4x4xf32) <- (-1x160x-1x-1xf32, 2xi64)
        pool2d_10 = paddle._C_ops.pool2d(add_9, full_int_array_32, [1, 1], [0, 0], False, True, 'NCHW', 'avg', False, True, 'EXPLICIT')

        # pd_op.conv2d: (-1x128x4x4xf32) <- (-1x160x4x4xf32, 128x160x1x1xf32)
        conv2d_49 = paddle._C_ops.conv2d(pool2d_10, parameter_274, [1, 1], [0, 0], 'SAME', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_33 = [1, 128, 1, 1]

        # pd_op.reshape: (1x128x1x1xf32, 0x128xf32) <- (128xf32, 4xi64)
        reshape_36, reshape_37 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_275, full_int_array_33), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add_: (-1x128x4x4xf32) <- (-1x128x4x4xf32, 1x128x1x1xf32)
        add__18 = paddle._C_ops.add_(conv2d_49, reshape_36)

        # pd_op.batch_norm_: (-1x128x4x4xf32, 128xf32, 128xf32, xf32, xf32, None) <- (-1x128x4x4xf32, 128xf32, 128xf32, 128xf32, 128xf32)
        batch_norm__288, batch_norm__289, batch_norm__290, batch_norm__291, batch_norm__292, batch_norm__293 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(add__18, parameter_276, parameter_277, parameter_278, parameter_279, True, float('0.9'), float('1e-05'), 'NCHW', True, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x128x4x4xf32) <- (-1x128x4x4xf32)
        relu__10 = paddle._C_ops.relu_(batch_norm__288)

        # pd_op.cast_: (2xi32) <- (2xi32)
        cast__0 = paddle._C_ops.cast_(slice_1, paddle.int32)

        # pd_op.bilinear_interp: (-1x128x-1x-1xf32) <- (-1x128x4x4xf32, 2xi32, None, None)
        bilinear_interp_2 = paddle._C_ops.bilinear_interp(relu__10, cast__0, None, None, 'NCHW', -1, -1, -1, [], 'bilinear', False, 0)

        # pd_op.add: (-1x128x-1x-1xf32) <- (-1x128x-1x-1xf32, -1x128x-1x-1xf32)
        add_11 = paddle._C_ops.add(add_10, bilinear_interp_2)

        # pd_op.depthwise_conv2d: (-1x128x-1x-1xf32) <- (-1x128x-1x-1xf32, 128x1x3x3xf32)
        depthwise_conv2d_15 = paddle._C_ops.depthwise_conv2d(add_11, parameter_280, [1, 1], [0, 0], 'SAME', 128, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x128x-1x-1xf32, 128xf32, 128xf32, xf32, xf32, None) <- (-1x128x-1x-1xf32, 128xf32, 128xf32, 128xf32, 128xf32)
        batch_norm__294, batch_norm__295, batch_norm__296, batch_norm__297, batch_norm__298, batch_norm__299 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_15, parameter_281, parameter_282, parameter_283, parameter_284, True, float('0.9'), float('1e-05'), 'NCHW', True, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x128x-1x-1xf32) <- (-1x128x-1x-1xf32, 128x128x1x1xf32)
        conv2d_50 = paddle._C_ops.conv2d(batch_norm__294, parameter_285, [1, 1], [0, 0], 'SAME', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_34 = [1, 128, 1, 1]

        # pd_op.reshape: (1x128x1x1xf32, 0x128xf32) <- (128xf32, 4xi64)
        reshape_38, reshape_39 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_286, full_int_array_34), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add: (-1x128x-1x-1xf32) <- (-1x128x-1x-1xf32, 1x128x1x1xf32)
        add_12 = paddle._C_ops.add(conv2d_50, reshape_38)

        # pd_op.batch_norm_: (-1x128x-1x-1xf32, 128xf32, 128xf32, xf32, xf32, None) <- (-1x128x-1x-1xf32, 128xf32, 128xf32, 128xf32, 128xf32)
        batch_norm__300, batch_norm__301, batch_norm__302, batch_norm__303, batch_norm__304, batch_norm__305 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(add_12, parameter_287, parameter_288, parameter_289, parameter_290, True, float('0.9'), float('1e-05'), 'NCHW', True, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu: (-1x128x-1x-1xf32) <- (-1x128x-1x-1xf32)
        relu_12 = paddle._C_ops.relu(batch_norm__300)

        # pd_op.depthwise_conv2d: (-1x160x-1x-1xf32) <- (-1x160x-1x-1xf32, 160x1x3x3xf32)
        depthwise_conv2d_16 = paddle._C_ops.depthwise_conv2d(add_9, parameter_291, [1, 1], [1, 1], 'EXPLICIT', 160, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x160x-1x-1xf32, 160xf32, 160xf32, xf32, xf32, None) <- (-1x160x-1x-1xf32, 160xf32, 160xf32, 160xf32, 160xf32)
        batch_norm__306, batch_norm__307, batch_norm__308, batch_norm__309, batch_norm__310, batch_norm__311 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_16, parameter_292, parameter_293, parameter_294, parameter_295, True, float('0.9'), float('1e-05'), 'NCHW', True, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x128x-1x-1xf32) <- (-1x160x-1x-1xf32, 128x160x1x1xf32)
        conv2d_51 = paddle._C_ops.conv2d(batch_norm__306, parameter_296, [1, 1], [0, 0], 'SAME', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_35 = [1, 128, 1, 1]

        # pd_op.reshape: (1x128x1x1xf32, 0x128xf32) <- (128xf32, 4xi64)
        reshape_40, reshape_41 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_297, full_int_array_35), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add: (-1x128x-1x-1xf32) <- (-1x128x-1x-1xf32, 1x128x1x1xf32)
        add_13 = paddle._C_ops.add(conv2d_51, reshape_40)

        # pd_op.batch_norm_: (-1x128x-1x-1xf32, 128xf32, 128xf32, xf32, xf32, None) <- (-1x128x-1x-1xf32, 128xf32, 128xf32, 128xf32, 128xf32)
        batch_norm__312, batch_norm__313, batch_norm__314, batch_norm__315, batch_norm__316, batch_norm__317 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(add_13, parameter_298, parameter_299, parameter_300, parameter_301, True, float('0.9'), float('1e-05'), 'NCHW', True, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu: (-1x128x-1x-1xf32) <- (-1x128x-1x-1xf32)
        relu_13 = paddle._C_ops.relu(batch_norm__312)

        # pd_op.shape: (4xi32) <- (-1x128x-1x-1xf32)
        shape_2 = paddle._C_ops.shape(relu_13)

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_36 = [2]

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_37 = [2147483647]

        # pd_op.slice: (2xi32) <- (4xi32, 1xi64, 1xi64)
        slice_2 = paddle._C_ops.slice(shape_2, [0], full_int_array_36, full_int_array_37, [1], [])

        # pd_op.cast_: (2xi32) <- (2xi32)
        cast__1 = paddle._C_ops.cast_(slice_2, paddle.int32)

        # pd_op.bilinear_interp: (-1x128x-1x-1xf32) <- (-1x128x-1x-1xf32, 2xi32, None, None)
        bilinear_interp_3 = paddle._C_ops.bilinear_interp(relu_12, cast__1, None, None, 'NCHW', -1, -1, -1, [], 'bilinear', False, 0)

        # pd_op.add: (-1x128x-1x-1xf32) <- (-1x128x-1x-1xf32, -1x128x-1x-1xf32)
        add_14 = paddle._C_ops.add(relu_13, bilinear_interp_3)

        # pd_op.depthwise_conv2d: (-1x128x-1x-1xf32) <- (-1x128x-1x-1xf32, 128x1x3x3xf32)
        depthwise_conv2d_17 = paddle._C_ops.depthwise_conv2d(add_14, parameter_302, [1, 1], [1, 1], 'EXPLICIT', 128, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x128x-1x-1xf32, 128xf32, 128xf32, xf32, xf32, None) <- (-1x128x-1x-1xf32, 128xf32, 128xf32, 128xf32, 128xf32)
        batch_norm__318, batch_norm__319, batch_norm__320, batch_norm__321, batch_norm__322, batch_norm__323 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_17, parameter_303, parameter_304, parameter_305, parameter_306, True, float('0.9'), float('1e-05'), 'NCHW', True, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x128x-1x-1xf32) <- (-1x128x-1x-1xf32, 128x128x1x1xf32)
        conv2d_52 = paddle._C_ops.conv2d(batch_norm__318, parameter_307, [1, 1], [0, 0], 'SAME', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_38 = [1, 128, 1, 1]

        # pd_op.reshape: (1x128x1x1xf32, 0x128xf32) <- (128xf32, 4xi64)
        reshape_42, reshape_43 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_308, full_int_array_38), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add: (-1x128x-1x-1xf32) <- (-1x128x-1x-1xf32, 1x128x1x1xf32)
        add_15 = paddle._C_ops.add(conv2d_52, reshape_42)

        # pd_op.batch_norm_: (-1x128x-1x-1xf32, 128xf32, 128xf32, xf32, xf32, None) <- (-1x128x-1x-1xf32, 128xf32, 128xf32, 128xf32, 128xf32)
        batch_norm__324, batch_norm__325, batch_norm__326, batch_norm__327, batch_norm__328, batch_norm__329 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(add_15, parameter_309, parameter_310, parameter_311, parameter_312, True, float('0.9'), float('1e-05'), 'NCHW', True, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu: (-1x128x-1x-1xf32) <- (-1x128x-1x-1xf32)
        relu_14 = paddle._C_ops.relu(batch_norm__324)

        # pd_op.depthwise_conv2d: (-1x112x-1x-1xf32) <- (-1x112x-1x-1xf32, 112x1x3x3xf32)
        depthwise_conv2d_18 = paddle._C_ops.depthwise_conv2d(add_7, parameter_313, [1, 1], [1, 1], 'EXPLICIT', 112, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x112x-1x-1xf32, 112xf32, 112xf32, xf32, xf32, None) <- (-1x112x-1x-1xf32, 112xf32, 112xf32, 112xf32, 112xf32)
        batch_norm__330, batch_norm__331, batch_norm__332, batch_norm__333, batch_norm__334, batch_norm__335 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_18, parameter_314, parameter_315, parameter_316, parameter_317, True, float('0.9'), float('1e-05'), 'NCHW', True, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x128x-1x-1xf32) <- (-1x112x-1x-1xf32, 128x112x1x1xf32)
        conv2d_53 = paddle._C_ops.conv2d(batch_norm__330, parameter_318, [1, 1], [0, 0], 'SAME', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_39 = [1, 128, 1, 1]

        # pd_op.reshape: (1x128x1x1xf32, 0x128xf32) <- (128xf32, 4xi64)
        reshape_44, reshape_45 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_319, full_int_array_39), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add: (-1x128x-1x-1xf32) <- (-1x128x-1x-1xf32, 1x128x1x1xf32)
        add_16 = paddle._C_ops.add(conv2d_53, reshape_44)

        # pd_op.batch_norm_: (-1x128x-1x-1xf32, 128xf32, 128xf32, xf32, xf32, None) <- (-1x128x-1x-1xf32, 128xf32, 128xf32, 128xf32, 128xf32)
        batch_norm__336, batch_norm__337, batch_norm__338, batch_norm__339, batch_norm__340, batch_norm__341 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(add_16, parameter_320, parameter_321, parameter_322, parameter_323, True, float('0.9'), float('1e-05'), 'NCHW', True, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu: (-1x128x-1x-1xf32) <- (-1x128x-1x-1xf32)
        relu_15 = paddle._C_ops.relu(batch_norm__336)

        # pd_op.shape: (4xi32) <- (-1x128x-1x-1xf32)
        shape_3 = paddle._C_ops.shape(relu_15)

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_40 = [2]

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_41 = [2147483647]

        # pd_op.slice: (2xi32) <- (4xi32, 1xi64, 1xi64)
        slice_3 = paddle._C_ops.slice(shape_3, [0], full_int_array_40, full_int_array_41, [1], [])

        # pd_op.cast_: (2xi32) <- (2xi32)
        cast__2 = paddle._C_ops.cast_(slice_3, paddle.int32)

        # pd_op.bilinear_interp: (-1x128x-1x-1xf32) <- (-1x128x-1x-1xf32, 2xi32, None, None)
        bilinear_interp_4 = paddle._C_ops.bilinear_interp(relu_14, cast__2, None, None, 'NCHW', -1, -1, -1, [], 'bilinear', False, 0)

        # pd_op.add: (-1x128x-1x-1xf32) <- (-1x128x-1x-1xf32, -1x128x-1x-1xf32)
        add_17 = paddle._C_ops.add(relu_15, bilinear_interp_4)

        # pd_op.depthwise_conv2d: (-1x128x-1x-1xf32) <- (-1x128x-1x-1xf32, 128x1x3x3xf32)
        depthwise_conv2d_19 = paddle._C_ops.depthwise_conv2d(add_17, parameter_324, [1, 1], [1, 1], 'EXPLICIT', 128, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x128x-1x-1xf32, 128xf32, 128xf32, xf32, xf32, None) <- (-1x128x-1x-1xf32, 128xf32, 128xf32, 128xf32, 128xf32)
        batch_norm__342, batch_norm__343, batch_norm__344, batch_norm__345, batch_norm__346, batch_norm__347 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_19, parameter_325, parameter_326, parameter_327, parameter_328, True, float('0.9'), float('1e-05'), 'NCHW', True, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x64x-1x-1xf32) <- (-1x128x-1x-1xf32, 64x128x1x1xf32)
        conv2d_54 = paddle._C_ops.conv2d(batch_norm__342, parameter_329, [1, 1], [0, 0], 'SAME', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_42 = [1, 64, 1, 1]

        # pd_op.reshape: (1x64x1x1xf32, 0x64xf32) <- (64xf32, 4xi64)
        reshape_46, reshape_47 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_330, full_int_array_42), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add: (-1x64x-1x-1xf32) <- (-1x64x-1x-1xf32, 1x64x1x1xf32)
        add_18 = paddle._C_ops.add(conv2d_54, reshape_46)

        # pd_op.batch_norm_: (-1x64x-1x-1xf32, 64xf32, 64xf32, xf32, xf32, None) <- (-1x64x-1x-1xf32, 64xf32, 64xf32, 64xf32, 64xf32)
        batch_norm__348, batch_norm__349, batch_norm__350, batch_norm__351, batch_norm__352, batch_norm__353 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(add_18, parameter_331, parameter_332, parameter_333, parameter_334, True, float('0.9'), float('1e-05'), 'NCHW', True, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu: (-1x64x-1x-1xf32) <- (-1x64x-1x-1xf32)
        relu_16 = paddle._C_ops.relu(batch_norm__348)

        # pd_op.depthwise_conv2d: (-1x40x-1x-1xf32) <- (-1x40x-1x-1xf32, 40x1x3x3xf32)
        depthwise_conv2d_20 = paddle._C_ops.depthwise_conv2d(add_3, parameter_335, [1, 1], [1, 1], 'EXPLICIT', 40, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x40x-1x-1xf32, 40xf32, 40xf32, xf32, xf32, None) <- (-1x40x-1x-1xf32, 40xf32, 40xf32, 40xf32, 40xf32)
        batch_norm__354, batch_norm__355, batch_norm__356, batch_norm__357, batch_norm__358, batch_norm__359 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_20, parameter_336, parameter_337, parameter_338, parameter_339, True, float('0.9'), float('1e-05'), 'NCHW', True, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x64x-1x-1xf32) <- (-1x40x-1x-1xf32, 64x40x1x1xf32)
        conv2d_55 = paddle._C_ops.conv2d(batch_norm__354, parameter_340, [1, 1], [0, 0], 'SAME', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_43 = [1, 64, 1, 1]

        # pd_op.reshape: (1x64x1x1xf32, 0x64xf32) <- (64xf32, 4xi64)
        reshape_48, reshape_49 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_341, full_int_array_43), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add: (-1x64x-1x-1xf32) <- (-1x64x-1x-1xf32, 1x64x1x1xf32)
        add_19 = paddle._C_ops.add(conv2d_55, reshape_48)

        # pd_op.batch_norm_: (-1x64x-1x-1xf32, 64xf32, 64xf32, xf32, xf32, None) <- (-1x64x-1x-1xf32, 64xf32, 64xf32, 64xf32, 64xf32)
        batch_norm__360, batch_norm__361, batch_norm__362, batch_norm__363, batch_norm__364, batch_norm__365 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(add_19, parameter_342, parameter_343, parameter_344, parameter_345, True, float('0.9'), float('1e-05'), 'NCHW', True, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu: (-1x64x-1x-1xf32) <- (-1x64x-1x-1xf32)
        relu_17 = paddle._C_ops.relu(batch_norm__360)

        # pd_op.shape: (4xi32) <- (-1x64x-1x-1xf32)
        shape_4 = paddle._C_ops.shape(relu_17)

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_44 = [2]

        # pd_op.full_int_array: (1xi64) <- ()
        full_int_array_45 = [2147483647]

        # pd_op.slice: (2xi32) <- (4xi32, 1xi64, 1xi64)
        slice_4 = paddle._C_ops.slice(shape_4, [0], full_int_array_44, full_int_array_45, [1], [])

        # pd_op.cast_: (2xi32) <- (2xi32)
        cast__3 = paddle._C_ops.cast_(slice_4, paddle.int32)

        # pd_op.bilinear_interp: (-1x64x-1x-1xf32) <- (-1x64x-1x-1xf32, 2xi32, None, None)
        bilinear_interp_5 = paddle._C_ops.bilinear_interp(relu_16, cast__3, None, None, 'NCHW', -1, -1, -1, [], 'bilinear', False, 0)

        # pd_op.add: (-1x64x-1x-1xf32) <- (-1x64x-1x-1xf32, -1x64x-1x-1xf32)
        add_20 = paddle._C_ops.add(relu_17, bilinear_interp_5)

        # pd_op.depthwise_conv2d: (-1x64x-1x-1xf32) <- (-1x64x-1x-1xf32, 64x1x3x3xf32)
        depthwise_conv2d_21 = paddle._C_ops.depthwise_conv2d(add_20, parameter_346, [1, 1], [1, 1], 'EXPLICIT', 64, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x64x-1x-1xf32, 64xf32, 64xf32, xf32, xf32, None) <- (-1x64x-1x-1xf32, 64xf32, 64xf32, 64xf32, 64xf32)
        batch_norm__366, batch_norm__367, batch_norm__368, batch_norm__369, batch_norm__370, batch_norm__371 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_21, parameter_347, parameter_348, parameter_349, parameter_350, True, float('0.9'), float('1e-05'), 'NCHW', True, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x32x-1x-1xf32) <- (-1x64x-1x-1xf32, 32x64x1x1xf32)
        conv2d_56 = paddle._C_ops.conv2d(batch_norm__366, parameter_351, [1, 1], [0, 0], 'SAME', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_46 = [1, 32, 1, 1]

        # pd_op.reshape: (1x32x1x1xf32, 0x32xf32) <- (32xf32, 4xi64)
        reshape_50, reshape_51 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_352, full_int_array_46), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add: (-1x32x-1x-1xf32) <- (-1x32x-1x-1xf32, 1x32x1x1xf32)
        add_21 = paddle._C_ops.add(conv2d_56, reshape_50)

        # pd_op.batch_norm_: (-1x32x-1x-1xf32, 32xf32, 32xf32, xf32, xf32, None) <- (-1x32x-1x-1xf32, 32xf32, 32xf32, 32xf32, 32xf32)
        batch_norm__372, batch_norm__373, batch_norm__374, batch_norm__375, batch_norm__376, batch_norm__377 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(add_21, parameter_353, parameter_354, parameter_355, parameter_356, True, float('0.9'), float('1e-05'), 'NCHW', True, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu: (-1x32x-1x-1xf32) <- (-1x32x-1x-1xf32)
        relu_18 = paddle._C_ops.relu(batch_norm__372)

        # pd_op.depthwise_conv2d: (-1x32x-1x-1xf32) <- (-1x32x-1x-1xf32, 32x1x3x3xf32)
        depthwise_conv2d_22 = paddle._C_ops.depthwise_conv2d(relu_18, parameter_357, [1, 1], [0, 0], 'SAME', 32, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x32x-1x-1xf32, 32xf32, 32xf32, xf32, xf32, None) <- (-1x32x-1x-1xf32, 32xf32, 32xf32, 32xf32, 32xf32)
        batch_norm__378, batch_norm__379, batch_norm__380, batch_norm__381, batch_norm__382, batch_norm__383 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_22, parameter_358, parameter_359, parameter_360, parameter_361, True, float('0.9'), float('1e-05'), 'NCHW', True, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x32x-1x-1xf32) <- (-1x32x-1x-1xf32, 32x32x1x1xf32)
        conv2d_57 = paddle._C_ops.conv2d(batch_norm__378, parameter_362, [1, 1], [0, 0], 'SAME', [1, 1], 1, 'NCHW')

        # pd_op.full_int_array: (4xi64) <- ()
        full_int_array_47 = [1, 32, 1, 1]

        # pd_op.reshape: (1x32x1x1xf32, 0x32xf32) <- (32xf32, 4xi64)
        reshape_52, reshape_53 = (lambda x, f: f(x))(paddle._C_ops.reshape(parameter_363, full_int_array_47), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.add: (-1x32x-1x-1xf32) <- (-1x32x-1x-1xf32, 1x32x1x1xf32)
        add_22 = paddle._C_ops.add(conv2d_57, reshape_52)

        # pd_op.batch_norm_: (-1x32x-1x-1xf32, 32xf32, 32xf32, xf32, xf32, None) <- (-1x32x-1x-1xf32, 32xf32, 32xf32, 32xf32, 32xf32)
        batch_norm__384, batch_norm__385, batch_norm__386, batch_norm__387, batch_norm__388, batch_norm__389 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(add_22, parameter_364, parameter_365, parameter_366, parameter_367, True, float('0.9'), float('1e-05'), 'NCHW', True, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu: (-1x32x-1x-1xf32) <- (-1x32x-1x-1xf32)
        relu_19 = paddle._C_ops.relu(batch_norm__384)

        # pd_op.conv2d: (-1x19x-1x-1xf32) <- (-1x32x-1x-1xf32, 19x32x1x1xf32)
        conv2d_58 = paddle._C_ops.conv2d(relu_19, parameter_368, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.cast_: (2xi32) <- (2xi32)
        cast__4 = paddle._C_ops.cast_(slice_0, paddle.int32)

        # pd_op.bilinear_interp: (-1x19x-1x-1xf32) <- (-1x19x-1x-1xf32, 2xi32, None, None)
        bilinear_interp_6 = paddle._C_ops.bilinear_interp(conv2d_58, cast__4, None, None, 'NCHW', -1, -1, -1, [], 'bilinear', False, 0)

        # pd_op.full: (1xi64) <- ()
        full_0 = paddle._C_ops.full([1], float('1'), paddle.int64, paddle.core.CPUPlace())

        # pd_op.argmax: (-1x-1x-1xi32) <- (-1x19x-1x-1xf32, 1xi64)
        argmax_0 = paddle._C_ops.argmax(bilinear_interp_6, full_0, False, False, paddle.int32)

        # pd_op.full: (1xf32) <- ()
        full_1 = paddle._C_ops.full([1], float('1'), paddle.float32, paddle.core.CPUPlace())

        # pd_op.scale: (-1x-1x-1xi32) <- (-1x-1x-1xi32, 1xf32)
        scale_0 = paddle._C_ops.scale(argmax_0, full_1, float('0'), True)
        return scale_0



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

    def forward(self, parameter_0, parameter_4, parameter_1, parameter_3, parameter_2, parameter_5, parameter_9, parameter_6, parameter_8, parameter_7, parameter_10, parameter_14, parameter_11, parameter_13, parameter_12, parameter_15, parameter_19, parameter_16, parameter_18, parameter_17, parameter_20, parameter_24, parameter_21, parameter_23, parameter_22, parameter_25, parameter_29, parameter_26, parameter_28, parameter_27, parameter_30, parameter_34, parameter_31, parameter_33, parameter_32, parameter_35, parameter_39, parameter_36, parameter_38, parameter_37, parameter_40, parameter_44, parameter_41, parameter_43, parameter_42, parameter_45, parameter_49, parameter_46, parameter_48, parameter_47, parameter_50, parameter_54, parameter_51, parameter_53, parameter_52, parameter_55, parameter_59, parameter_56, parameter_58, parameter_57, parameter_60, parameter_61, parameter_62, parameter_63, parameter_64, parameter_68, parameter_65, parameter_67, parameter_66, parameter_69, parameter_73, parameter_70, parameter_72, parameter_71, parameter_74, parameter_78, parameter_75, parameter_77, parameter_76, parameter_79, parameter_80, parameter_81, parameter_82, parameter_83, parameter_87, parameter_84, parameter_86, parameter_85, parameter_88, parameter_92, parameter_89, parameter_91, parameter_90, parameter_93, parameter_97, parameter_94, parameter_96, parameter_95, parameter_98, parameter_99, parameter_100, parameter_101, parameter_102, parameter_106, parameter_103, parameter_105, parameter_104, parameter_107, parameter_111, parameter_108, parameter_110, parameter_109, parameter_112, parameter_116, parameter_113, parameter_115, parameter_114, parameter_117, parameter_121, parameter_118, parameter_120, parameter_119, parameter_122, parameter_126, parameter_123, parameter_125, parameter_124, parameter_127, parameter_131, parameter_128, parameter_130, parameter_129, parameter_132, parameter_136, parameter_133, parameter_135, parameter_134, parameter_137, parameter_141, parameter_138, parameter_140, parameter_139, parameter_142, parameter_146, parameter_143, parameter_145, parameter_144, parameter_147, parameter_151, parameter_148, parameter_150, parameter_149, parameter_152, parameter_156, parameter_153, parameter_155, parameter_154, parameter_157, parameter_161, parameter_158, parameter_160, parameter_159, parameter_162, parameter_166, parameter_163, parameter_165, parameter_164, parameter_167, parameter_171, parameter_168, parameter_170, parameter_169, parameter_172, parameter_176, parameter_173, parameter_175, parameter_174, parameter_177, parameter_178, parameter_179, parameter_180, parameter_181, parameter_185, parameter_182, parameter_184, parameter_183, parameter_186, parameter_190, parameter_187, parameter_189, parameter_188, parameter_191, parameter_195, parameter_192, parameter_194, parameter_193, parameter_196, parameter_197, parameter_198, parameter_199, parameter_200, parameter_204, parameter_201, parameter_203, parameter_202, parameter_205, parameter_209, parameter_206, parameter_208, parameter_207, parameter_210, parameter_214, parameter_211, parameter_213, parameter_212, parameter_215, parameter_216, parameter_217, parameter_218, parameter_219, parameter_223, parameter_220, parameter_222, parameter_221, parameter_224, parameter_228, parameter_225, parameter_227, parameter_226, parameter_229, parameter_233, parameter_230, parameter_232, parameter_231, parameter_234, parameter_235, parameter_236, parameter_237, parameter_238, parameter_242, parameter_239, parameter_241, parameter_240, parameter_243, parameter_247, parameter_244, parameter_246, parameter_245, parameter_248, parameter_252, parameter_249, parameter_251, parameter_250, parameter_253, parameter_254, parameter_255, parameter_256, parameter_257, parameter_261, parameter_258, parameter_260, parameter_259, parameter_262, parameter_263, parameter_267, parameter_264, parameter_266, parameter_265, parameter_268, parameter_269, parameter_273, parameter_270, parameter_272, parameter_271, parameter_274, parameter_275, parameter_279, parameter_276, parameter_278, parameter_277, parameter_280, parameter_284, parameter_281, parameter_283, parameter_282, parameter_285, parameter_286, parameter_290, parameter_287, parameter_289, parameter_288, parameter_291, parameter_295, parameter_292, parameter_294, parameter_293, parameter_296, parameter_297, parameter_301, parameter_298, parameter_300, parameter_299, parameter_302, parameter_306, parameter_303, parameter_305, parameter_304, parameter_307, parameter_308, parameter_312, parameter_309, parameter_311, parameter_310, parameter_313, parameter_317, parameter_314, parameter_316, parameter_315, parameter_318, parameter_319, parameter_323, parameter_320, parameter_322, parameter_321, parameter_324, parameter_328, parameter_325, parameter_327, parameter_326, parameter_329, parameter_330, parameter_334, parameter_331, parameter_333, parameter_332, parameter_335, parameter_339, parameter_336, parameter_338, parameter_337, parameter_340, parameter_341, parameter_345, parameter_342, parameter_344, parameter_343, parameter_346, parameter_350, parameter_347, parameter_349, parameter_348, parameter_351, parameter_352, parameter_356, parameter_353, parameter_355, parameter_354, parameter_357, parameter_361, parameter_358, parameter_360, parameter_359, parameter_362, parameter_363, parameter_367, parameter_364, parameter_366, parameter_365, parameter_368, feed_0):
        return self.builtin_module_745_0_0(parameter_0, parameter_4, parameter_1, parameter_3, parameter_2, parameter_5, parameter_9, parameter_6, parameter_8, parameter_7, parameter_10, parameter_14, parameter_11, parameter_13, parameter_12, parameter_15, parameter_19, parameter_16, parameter_18, parameter_17, parameter_20, parameter_24, parameter_21, parameter_23, parameter_22, parameter_25, parameter_29, parameter_26, parameter_28, parameter_27, parameter_30, parameter_34, parameter_31, parameter_33, parameter_32, parameter_35, parameter_39, parameter_36, parameter_38, parameter_37, parameter_40, parameter_44, parameter_41, parameter_43, parameter_42, parameter_45, parameter_49, parameter_46, parameter_48, parameter_47, parameter_50, parameter_54, parameter_51, parameter_53, parameter_52, parameter_55, parameter_59, parameter_56, parameter_58, parameter_57, parameter_60, parameter_61, parameter_62, parameter_63, parameter_64, parameter_68, parameter_65, parameter_67, parameter_66, parameter_69, parameter_73, parameter_70, parameter_72, parameter_71, parameter_74, parameter_78, parameter_75, parameter_77, parameter_76, parameter_79, parameter_80, parameter_81, parameter_82, parameter_83, parameter_87, parameter_84, parameter_86, parameter_85, parameter_88, parameter_92, parameter_89, parameter_91, parameter_90, parameter_93, parameter_97, parameter_94, parameter_96, parameter_95, parameter_98, parameter_99, parameter_100, parameter_101, parameter_102, parameter_106, parameter_103, parameter_105, parameter_104, parameter_107, parameter_111, parameter_108, parameter_110, parameter_109, parameter_112, parameter_116, parameter_113, parameter_115, parameter_114, parameter_117, parameter_121, parameter_118, parameter_120, parameter_119, parameter_122, parameter_126, parameter_123, parameter_125, parameter_124, parameter_127, parameter_131, parameter_128, parameter_130, parameter_129, parameter_132, parameter_136, parameter_133, parameter_135, parameter_134, parameter_137, parameter_141, parameter_138, parameter_140, parameter_139, parameter_142, parameter_146, parameter_143, parameter_145, parameter_144, parameter_147, parameter_151, parameter_148, parameter_150, parameter_149, parameter_152, parameter_156, parameter_153, parameter_155, parameter_154, parameter_157, parameter_161, parameter_158, parameter_160, parameter_159, parameter_162, parameter_166, parameter_163, parameter_165, parameter_164, parameter_167, parameter_171, parameter_168, parameter_170, parameter_169, parameter_172, parameter_176, parameter_173, parameter_175, parameter_174, parameter_177, parameter_178, parameter_179, parameter_180, parameter_181, parameter_185, parameter_182, parameter_184, parameter_183, parameter_186, parameter_190, parameter_187, parameter_189, parameter_188, parameter_191, parameter_195, parameter_192, parameter_194, parameter_193, parameter_196, parameter_197, parameter_198, parameter_199, parameter_200, parameter_204, parameter_201, parameter_203, parameter_202, parameter_205, parameter_209, parameter_206, parameter_208, parameter_207, parameter_210, parameter_214, parameter_211, parameter_213, parameter_212, parameter_215, parameter_216, parameter_217, parameter_218, parameter_219, parameter_223, parameter_220, parameter_222, parameter_221, parameter_224, parameter_228, parameter_225, parameter_227, parameter_226, parameter_229, parameter_233, parameter_230, parameter_232, parameter_231, parameter_234, parameter_235, parameter_236, parameter_237, parameter_238, parameter_242, parameter_239, parameter_241, parameter_240, parameter_243, parameter_247, parameter_244, parameter_246, parameter_245, parameter_248, parameter_252, parameter_249, parameter_251, parameter_250, parameter_253, parameter_254, parameter_255, parameter_256, parameter_257, parameter_261, parameter_258, parameter_260, parameter_259, parameter_262, parameter_263, parameter_267, parameter_264, parameter_266, parameter_265, parameter_268, parameter_269, parameter_273, parameter_270, parameter_272, parameter_271, parameter_274, parameter_275, parameter_279, parameter_276, parameter_278, parameter_277, parameter_280, parameter_284, parameter_281, parameter_283, parameter_282, parameter_285, parameter_286, parameter_290, parameter_287, parameter_289, parameter_288, parameter_291, parameter_295, parameter_292, parameter_294, parameter_293, parameter_296, parameter_297, parameter_301, parameter_298, parameter_300, parameter_299, parameter_302, parameter_306, parameter_303, parameter_305, parameter_304, parameter_307, parameter_308, parameter_312, parameter_309, parameter_311, parameter_310, parameter_313, parameter_317, parameter_314, parameter_316, parameter_315, parameter_318, parameter_319, parameter_323, parameter_320, parameter_322, parameter_321, parameter_324, parameter_328, parameter_325, parameter_327, parameter_326, parameter_329, parameter_330, parameter_334, parameter_331, parameter_333, parameter_332, parameter_335, parameter_339, parameter_336, parameter_338, parameter_337, parameter_340, parameter_341, parameter_345, parameter_342, parameter_344, parameter_343, parameter_346, parameter_350, parameter_347, parameter_349, parameter_348, parameter_351, parameter_352, parameter_356, parameter_353, parameter_355, parameter_354, parameter_357, parameter_361, parameter_358, parameter_360, parameter_359, parameter_362, parameter_363, parameter_367, parameter_364, parameter_366, parameter_365, parameter_368, feed_0)

@unittest.skipIf(need_skip, skip_message)
class Test_builtin_module_745_0_0(CinnTestBase, unittest.TestCase):
    def prepare_data(self):
        self.inputs = [
            # parameter_0
            paddle.uniform([16, 3, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_4
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_1
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_3
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_2
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_5
            paddle.uniform([16, 16, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_9
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_6
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_8
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_7
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_10
            paddle.uniform([16, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_14
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_11
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_13
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_12
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_15
            paddle.uniform([16, 16, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_19
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_16
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_18
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_17
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_20
            paddle.uniform([64, 16, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_24
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_21
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_23
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_22
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_25
            paddle.uniform([64, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_29
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_26
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_28
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_27
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_30
            paddle.uniform([24, 64, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_34
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_31
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_33
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_32
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_35
            paddle.uniform([72, 24, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_39
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_36
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_38
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_37
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_40
            paddle.uniform([72, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_44
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_41
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_43
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_42
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_45
            paddle.uniform([24, 72, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_49
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_46
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_48
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_47
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_50
            paddle.uniform([72, 24, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_54
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_51
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_53
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_52
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_55
            paddle.uniform([72, 1, 5, 5], dtype='float32', min=0, max=0.5),
            # parameter_59
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_56
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_58
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_57
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_60
            paddle.uniform([18, 72, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_61
            paddle.uniform([18], dtype='float32', min=0, max=0.5),
            # parameter_62
            paddle.uniform([72, 18, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_63
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_64
            paddle.uniform([40, 72, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_68
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_65
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_67
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_66
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_69
            paddle.uniform([120, 40, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_73
            paddle.uniform([120], dtype='float32', min=0, max=0.5),
            # parameter_70
            paddle.uniform([120], dtype='float32', min=0, max=0.5),
            # parameter_72
            paddle.uniform([120], dtype='float32', min=0, max=0.5),
            # parameter_71
            paddle.uniform([120], dtype='float32', min=0, max=0.5),
            # parameter_74
            paddle.uniform([120, 1, 5, 5], dtype='float32', min=0, max=0.5),
            # parameter_78
            paddle.uniform([120], dtype='float32', min=0, max=0.5),
            # parameter_75
            paddle.uniform([120], dtype='float32', min=0, max=0.5),
            # parameter_77
            paddle.uniform([120], dtype='float32', min=0, max=0.5),
            # parameter_76
            paddle.uniform([120], dtype='float32', min=0, max=0.5),
            # parameter_79
            paddle.uniform([30, 120, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_80
            paddle.uniform([30], dtype='float32', min=0, max=0.5),
            # parameter_81
            paddle.uniform([120, 30, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_82
            paddle.uniform([120], dtype='float32', min=0, max=0.5),
            # parameter_83
            paddle.uniform([40, 120, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_87
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_84
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_86
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_85
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_88
            paddle.uniform([120, 40, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_92
            paddle.uniform([120], dtype='float32', min=0, max=0.5),
            # parameter_89
            paddle.uniform([120], dtype='float32', min=0, max=0.5),
            # parameter_91
            paddle.uniform([120], dtype='float32', min=0, max=0.5),
            # parameter_90
            paddle.uniform([120], dtype='float32', min=0, max=0.5),
            # parameter_93
            paddle.uniform([120, 1, 5, 5], dtype='float32', min=0, max=0.5),
            # parameter_97
            paddle.uniform([120], dtype='float32', min=0, max=0.5),
            # parameter_94
            paddle.uniform([120], dtype='float32', min=0, max=0.5),
            # parameter_96
            paddle.uniform([120], dtype='float32', min=0, max=0.5),
            # parameter_95
            paddle.uniform([120], dtype='float32', min=0, max=0.5),
            # parameter_98
            paddle.uniform([30, 120, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_99
            paddle.uniform([30], dtype='float32', min=0, max=0.5),
            # parameter_100
            paddle.uniform([120, 30, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_101
            paddle.uniform([120], dtype='float32', min=0, max=0.5),
            # parameter_102
            paddle.uniform([40, 120, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_106
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_103
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_105
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_104
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_107
            paddle.uniform([240, 40, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_111
            paddle.uniform([240], dtype='float32', min=0, max=0.5),
            # parameter_108
            paddle.uniform([240], dtype='float32', min=0, max=0.5),
            # parameter_110
            paddle.uniform([240], dtype='float32', min=0, max=0.5),
            # parameter_109
            paddle.uniform([240], dtype='float32', min=0, max=0.5),
            # parameter_112
            paddle.uniform([240, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_116
            paddle.uniform([240], dtype='float32', min=0, max=0.5),
            # parameter_113
            paddle.uniform([240], dtype='float32', min=0, max=0.5),
            # parameter_115
            paddle.uniform([240], dtype='float32', min=0, max=0.5),
            # parameter_114
            paddle.uniform([240], dtype='float32', min=0, max=0.5),
            # parameter_117
            paddle.uniform([80, 240, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_121
            paddle.uniform([80], dtype='float32', min=0, max=0.5),
            # parameter_118
            paddle.uniform([80], dtype='float32', min=0, max=0.5),
            # parameter_120
            paddle.uniform([80], dtype='float32', min=0, max=0.5),
            # parameter_119
            paddle.uniform([80], dtype='float32', min=0, max=0.5),
            # parameter_122
            paddle.uniform([200, 80, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_126
            paddle.uniform([200], dtype='float32', min=0, max=0.5),
            # parameter_123
            paddle.uniform([200], dtype='float32', min=0, max=0.5),
            # parameter_125
            paddle.uniform([200], dtype='float32', min=0, max=0.5),
            # parameter_124
            paddle.uniform([200], dtype='float32', min=0, max=0.5),
            # parameter_127
            paddle.uniform([200, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_131
            paddle.uniform([200], dtype='float32', min=0, max=0.5),
            # parameter_128
            paddle.uniform([200], dtype='float32', min=0, max=0.5),
            # parameter_130
            paddle.uniform([200], dtype='float32', min=0, max=0.5),
            # parameter_129
            paddle.uniform([200], dtype='float32', min=0, max=0.5),
            # parameter_132
            paddle.uniform([80, 200, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_136
            paddle.uniform([80], dtype='float32', min=0, max=0.5),
            # parameter_133
            paddle.uniform([80], dtype='float32', min=0, max=0.5),
            # parameter_135
            paddle.uniform([80], dtype='float32', min=0, max=0.5),
            # parameter_134
            paddle.uniform([80], dtype='float32', min=0, max=0.5),
            # parameter_137
            paddle.uniform([184, 80, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_141
            paddle.uniform([184], dtype='float32', min=0, max=0.5),
            # parameter_138
            paddle.uniform([184], dtype='float32', min=0, max=0.5),
            # parameter_140
            paddle.uniform([184], dtype='float32', min=0, max=0.5),
            # parameter_139
            paddle.uniform([184], dtype='float32', min=0, max=0.5),
            # parameter_142
            paddle.uniform([184, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_146
            paddle.uniform([184], dtype='float32', min=0, max=0.5),
            # parameter_143
            paddle.uniform([184], dtype='float32', min=0, max=0.5),
            # parameter_145
            paddle.uniform([184], dtype='float32', min=0, max=0.5),
            # parameter_144
            paddle.uniform([184], dtype='float32', min=0, max=0.5),
            # parameter_147
            paddle.uniform([80, 184, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_151
            paddle.uniform([80], dtype='float32', min=0, max=0.5),
            # parameter_148
            paddle.uniform([80], dtype='float32', min=0, max=0.5),
            # parameter_150
            paddle.uniform([80], dtype='float32', min=0, max=0.5),
            # parameter_149
            paddle.uniform([80], dtype='float32', min=0, max=0.5),
            # parameter_152
            paddle.uniform([184, 80, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_156
            paddle.uniform([184], dtype='float32', min=0, max=0.5),
            # parameter_153
            paddle.uniform([184], dtype='float32', min=0, max=0.5),
            # parameter_155
            paddle.uniform([184], dtype='float32', min=0, max=0.5),
            # parameter_154
            paddle.uniform([184], dtype='float32', min=0, max=0.5),
            # parameter_157
            paddle.uniform([184, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_161
            paddle.uniform([184], dtype='float32', min=0, max=0.5),
            # parameter_158
            paddle.uniform([184], dtype='float32', min=0, max=0.5),
            # parameter_160
            paddle.uniform([184], dtype='float32', min=0, max=0.5),
            # parameter_159
            paddle.uniform([184], dtype='float32', min=0, max=0.5),
            # parameter_162
            paddle.uniform([80, 184, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_166
            paddle.uniform([80], dtype='float32', min=0, max=0.5),
            # parameter_163
            paddle.uniform([80], dtype='float32', min=0, max=0.5),
            # parameter_165
            paddle.uniform([80], dtype='float32', min=0, max=0.5),
            # parameter_164
            paddle.uniform([80], dtype='float32', min=0, max=0.5),
            # parameter_167
            paddle.uniform([480, 80, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_171
            paddle.uniform([480], dtype='float32', min=0, max=0.5),
            # parameter_168
            paddle.uniform([480], dtype='float32', min=0, max=0.5),
            # parameter_170
            paddle.uniform([480], dtype='float32', min=0, max=0.5),
            # parameter_169
            paddle.uniform([480], dtype='float32', min=0, max=0.5),
            # parameter_172
            paddle.uniform([480, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_176
            paddle.uniform([480], dtype='float32', min=0, max=0.5),
            # parameter_173
            paddle.uniform([480], dtype='float32', min=0, max=0.5),
            # parameter_175
            paddle.uniform([480], dtype='float32', min=0, max=0.5),
            # parameter_174
            paddle.uniform([480], dtype='float32', min=0, max=0.5),
            # parameter_177
            paddle.uniform([120, 480, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_178
            paddle.uniform([120], dtype='float32', min=0, max=0.5),
            # parameter_179
            paddle.uniform([480, 120, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_180
            paddle.uniform([480], dtype='float32', min=0, max=0.5),
            # parameter_181
            paddle.uniform([112, 480, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_185
            paddle.uniform([112], dtype='float32', min=0, max=0.5),
            # parameter_182
            paddle.uniform([112], dtype='float32', min=0, max=0.5),
            # parameter_184
            paddle.uniform([112], dtype='float32', min=0, max=0.5),
            # parameter_183
            paddle.uniform([112], dtype='float32', min=0, max=0.5),
            # parameter_186
            paddle.uniform([672, 112, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_190
            paddle.uniform([672], dtype='float32', min=0, max=0.5),
            # parameter_187
            paddle.uniform([672], dtype='float32', min=0, max=0.5),
            # parameter_189
            paddle.uniform([672], dtype='float32', min=0, max=0.5),
            # parameter_188
            paddle.uniform([672], dtype='float32', min=0, max=0.5),
            # parameter_191
            paddle.uniform([672, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_195
            paddle.uniform([672], dtype='float32', min=0, max=0.5),
            # parameter_192
            paddle.uniform([672], dtype='float32', min=0, max=0.5),
            # parameter_194
            paddle.uniform([672], dtype='float32', min=0, max=0.5),
            # parameter_193
            paddle.uniform([672], dtype='float32', min=0, max=0.5),
            # parameter_196
            paddle.uniform([168, 672, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_197
            paddle.uniform([168], dtype='float32', min=0, max=0.5),
            # parameter_198
            paddle.uniform([672, 168, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_199
            paddle.uniform([672], dtype='float32', min=0, max=0.5),
            # parameter_200
            paddle.uniform([112, 672, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_204
            paddle.uniform([112], dtype='float32', min=0, max=0.5),
            # parameter_201
            paddle.uniform([112], dtype='float32', min=0, max=0.5),
            # parameter_203
            paddle.uniform([112], dtype='float32', min=0, max=0.5),
            # parameter_202
            paddle.uniform([112], dtype='float32', min=0, max=0.5),
            # parameter_205
            paddle.uniform([672, 112, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_209
            paddle.uniform([672], dtype='float32', min=0, max=0.5),
            # parameter_206
            paddle.uniform([672], dtype='float32', min=0, max=0.5),
            # parameter_208
            paddle.uniform([672], dtype='float32', min=0, max=0.5),
            # parameter_207
            paddle.uniform([672], dtype='float32', min=0, max=0.5),
            # parameter_210
            paddle.uniform([672, 1, 5, 5], dtype='float32', min=0, max=0.5),
            # parameter_214
            paddle.uniform([672], dtype='float32', min=0, max=0.5),
            # parameter_211
            paddle.uniform([672], dtype='float32', min=0, max=0.5),
            # parameter_213
            paddle.uniform([672], dtype='float32', min=0, max=0.5),
            # parameter_212
            paddle.uniform([672], dtype='float32', min=0, max=0.5),
            # parameter_215
            paddle.uniform([168, 672, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_216
            paddle.uniform([168], dtype='float32', min=0, max=0.5),
            # parameter_217
            paddle.uniform([672, 168, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_218
            paddle.uniform([672], dtype='float32', min=0, max=0.5),
            # parameter_219
            paddle.uniform([160, 672, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_223
            paddle.uniform([160], dtype='float32', min=0, max=0.5),
            # parameter_220
            paddle.uniform([160], dtype='float32', min=0, max=0.5),
            # parameter_222
            paddle.uniform([160], dtype='float32', min=0, max=0.5),
            # parameter_221
            paddle.uniform([160], dtype='float32', min=0, max=0.5),
            # parameter_224
            paddle.uniform([960, 160, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_228
            paddle.uniform([960], dtype='float32', min=0, max=0.5),
            # parameter_225
            paddle.uniform([960], dtype='float32', min=0, max=0.5),
            # parameter_227
            paddle.uniform([960], dtype='float32', min=0, max=0.5),
            # parameter_226
            paddle.uniform([960], dtype='float32', min=0, max=0.5),
            # parameter_229
            paddle.uniform([960, 1, 5, 5], dtype='float32', min=0, max=0.5),
            # parameter_233
            paddle.uniform([960], dtype='float32', min=0, max=0.5),
            # parameter_230
            paddle.uniform([960], dtype='float32', min=0, max=0.5),
            # parameter_232
            paddle.uniform([960], dtype='float32', min=0, max=0.5),
            # parameter_231
            paddle.uniform([960], dtype='float32', min=0, max=0.5),
            # parameter_234
            paddle.uniform([240, 960, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_235
            paddle.uniform([240], dtype='float32', min=0, max=0.5),
            # parameter_236
            paddle.uniform([960, 240, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_237
            paddle.uniform([960], dtype='float32', min=0, max=0.5),
            # parameter_238
            paddle.uniform([160, 960, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_242
            paddle.uniform([160], dtype='float32', min=0, max=0.5),
            # parameter_239
            paddle.uniform([160], dtype='float32', min=0, max=0.5),
            # parameter_241
            paddle.uniform([160], dtype='float32', min=0, max=0.5),
            # parameter_240
            paddle.uniform([160], dtype='float32', min=0, max=0.5),
            # parameter_243
            paddle.uniform([960, 160, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_247
            paddle.uniform([960], dtype='float32', min=0, max=0.5),
            # parameter_244
            paddle.uniform([960], dtype='float32', min=0, max=0.5),
            # parameter_246
            paddle.uniform([960], dtype='float32', min=0, max=0.5),
            # parameter_245
            paddle.uniform([960], dtype='float32', min=0, max=0.5),
            # parameter_248
            paddle.uniform([960, 1, 5, 5], dtype='float32', min=0, max=0.5),
            # parameter_252
            paddle.uniform([960], dtype='float32', min=0, max=0.5),
            # parameter_249
            paddle.uniform([960], dtype='float32', min=0, max=0.5),
            # parameter_251
            paddle.uniform([960], dtype='float32', min=0, max=0.5),
            # parameter_250
            paddle.uniform([960], dtype='float32', min=0, max=0.5),
            # parameter_253
            paddle.uniform([240, 960, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_254
            paddle.uniform([240], dtype='float32', min=0, max=0.5),
            # parameter_255
            paddle.uniform([960, 240, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_256
            paddle.uniform([960], dtype='float32', min=0, max=0.5),
            # parameter_257
            paddle.uniform([160, 960, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_261
            paddle.uniform([160], dtype='float32', min=0, max=0.5),
            # parameter_258
            paddle.uniform([160], dtype='float32', min=0, max=0.5),
            # parameter_260
            paddle.uniform([160], dtype='float32', min=0, max=0.5),
            # parameter_259
            paddle.uniform([160], dtype='float32', min=0, max=0.5),
            # parameter_262
            paddle.uniform([128, 160, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_263
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_267
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_264
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_266
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_265
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_268
            paddle.uniform([128, 160, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_269
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_273
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_270
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_272
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_271
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_274
            paddle.uniform([128, 160, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_275
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_279
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_276
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_278
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_277
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_280
            paddle.uniform([128, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_284
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_281
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_283
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_282
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_285
            paddle.uniform([128, 128, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_286
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_290
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_287
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_289
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_288
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_291
            paddle.uniform([160, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_295
            paddle.uniform([160], dtype='float32', min=0, max=0.5),
            # parameter_292
            paddle.uniform([160], dtype='float32', min=0, max=0.5),
            # parameter_294
            paddle.uniform([160], dtype='float32', min=0, max=0.5),
            # parameter_293
            paddle.uniform([160], dtype='float32', min=0, max=0.5),
            # parameter_296
            paddle.uniform([128, 160, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_297
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_301
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_298
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_300
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_299
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_302
            paddle.uniform([128, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_306
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_303
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_305
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_304
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_307
            paddle.uniform([128, 128, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_308
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_312
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_309
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_311
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_310
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_313
            paddle.uniform([112, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_317
            paddle.uniform([112], dtype='float32', min=0, max=0.5),
            # parameter_314
            paddle.uniform([112], dtype='float32', min=0, max=0.5),
            # parameter_316
            paddle.uniform([112], dtype='float32', min=0, max=0.5),
            # parameter_315
            paddle.uniform([112], dtype='float32', min=0, max=0.5),
            # parameter_318
            paddle.uniform([128, 112, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_319
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_323
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_320
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_322
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_321
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_324
            paddle.uniform([128, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_328
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_325
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_327
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_326
            paddle.uniform([128], dtype='float32', min=0, max=0.5),
            # parameter_329
            paddle.uniform([64, 128, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_330
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_334
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_331
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_333
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_332
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_335
            paddle.uniform([40, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_339
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_336
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_338
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_337
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_340
            paddle.uniform([64, 40, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_341
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_345
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_342
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_344
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_343
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_346
            paddle.uniform([64, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_350
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_347
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_349
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_348
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_351
            paddle.uniform([32, 64, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_352
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_356
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_353
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_355
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_354
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_357
            paddle.uniform([32, 1, 3, 3], dtype='float32', min=0, max=0.5),
            # parameter_361
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_358
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_360
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_359
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_362
            paddle.uniform([32, 32, 1, 1], dtype='float32', min=0, max=0.5),
            # parameter_363
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_367
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_364
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_366
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_365
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_368
            paddle.uniform([19, 32, 1, 1], dtype='float32', min=0, max=0.5),
            # feed_0
            paddle.uniform([1, 3, 1024, 512], dtype='float32', min=0, max=0.5),
        ]
        for input in self.inputs:
            input.stop_gradient = True

    def apply_to_static(self, net, use_cinn):
        build_strategy = paddle.static.BuildStrategy()
        input_spec = [
            # parameter_0
            paddle.static.InputSpec(shape=[16, 3, 3, 3], dtype='float32'),
            # parameter_4
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_1
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_3
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_2
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_5
            paddle.static.InputSpec(shape=[16, 16, 1, 1], dtype='float32'),
            # parameter_9
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_6
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_8
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_7
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_10
            paddle.static.InputSpec(shape=[16, 1, 3, 3], dtype='float32'),
            # parameter_14
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_11
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_13
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_12
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_15
            paddle.static.InputSpec(shape=[16, 16, 1, 1], dtype='float32'),
            # parameter_19
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_16
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_18
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_17
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_20
            paddle.static.InputSpec(shape=[64, 16, 1, 1], dtype='float32'),
            # parameter_24
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_21
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_23
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_22
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_25
            paddle.static.InputSpec(shape=[64, 1, 3, 3], dtype='float32'),
            # parameter_29
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_26
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_28
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_27
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_30
            paddle.static.InputSpec(shape=[24, 64, 1, 1], dtype='float32'),
            # parameter_34
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_31
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_33
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_32
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_35
            paddle.static.InputSpec(shape=[72, 24, 1, 1], dtype='float32'),
            # parameter_39
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_36
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_38
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_37
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_40
            paddle.static.InputSpec(shape=[72, 1, 3, 3], dtype='float32'),
            # parameter_44
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_41
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_43
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_42
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_45
            paddle.static.InputSpec(shape=[24, 72, 1, 1], dtype='float32'),
            # parameter_49
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_46
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_48
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_47
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_50
            paddle.static.InputSpec(shape=[72, 24, 1, 1], dtype='float32'),
            # parameter_54
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_51
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_53
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_52
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_55
            paddle.static.InputSpec(shape=[72, 1, 5, 5], dtype='float32'),
            # parameter_59
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_56
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_58
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_57
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_60
            paddle.static.InputSpec(shape=[18, 72, 1, 1], dtype='float32'),
            # parameter_61
            paddle.static.InputSpec(shape=[18], dtype='float32'),
            # parameter_62
            paddle.static.InputSpec(shape=[72, 18, 1, 1], dtype='float32'),
            # parameter_63
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_64
            paddle.static.InputSpec(shape=[40, 72, 1, 1], dtype='float32'),
            # parameter_68
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_65
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_67
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_66
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_69
            paddle.static.InputSpec(shape=[120, 40, 1, 1], dtype='float32'),
            # parameter_73
            paddle.static.InputSpec(shape=[120], dtype='float32'),
            # parameter_70
            paddle.static.InputSpec(shape=[120], dtype='float32'),
            # parameter_72
            paddle.static.InputSpec(shape=[120], dtype='float32'),
            # parameter_71
            paddle.static.InputSpec(shape=[120], dtype='float32'),
            # parameter_74
            paddle.static.InputSpec(shape=[120, 1, 5, 5], dtype='float32'),
            # parameter_78
            paddle.static.InputSpec(shape=[120], dtype='float32'),
            # parameter_75
            paddle.static.InputSpec(shape=[120], dtype='float32'),
            # parameter_77
            paddle.static.InputSpec(shape=[120], dtype='float32'),
            # parameter_76
            paddle.static.InputSpec(shape=[120], dtype='float32'),
            # parameter_79
            paddle.static.InputSpec(shape=[30, 120, 1, 1], dtype='float32'),
            # parameter_80
            paddle.static.InputSpec(shape=[30], dtype='float32'),
            # parameter_81
            paddle.static.InputSpec(shape=[120, 30, 1, 1], dtype='float32'),
            # parameter_82
            paddle.static.InputSpec(shape=[120], dtype='float32'),
            # parameter_83
            paddle.static.InputSpec(shape=[40, 120, 1, 1], dtype='float32'),
            # parameter_87
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_84
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_86
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_85
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_88
            paddle.static.InputSpec(shape=[120, 40, 1, 1], dtype='float32'),
            # parameter_92
            paddle.static.InputSpec(shape=[120], dtype='float32'),
            # parameter_89
            paddle.static.InputSpec(shape=[120], dtype='float32'),
            # parameter_91
            paddle.static.InputSpec(shape=[120], dtype='float32'),
            # parameter_90
            paddle.static.InputSpec(shape=[120], dtype='float32'),
            # parameter_93
            paddle.static.InputSpec(shape=[120, 1, 5, 5], dtype='float32'),
            # parameter_97
            paddle.static.InputSpec(shape=[120], dtype='float32'),
            # parameter_94
            paddle.static.InputSpec(shape=[120], dtype='float32'),
            # parameter_96
            paddle.static.InputSpec(shape=[120], dtype='float32'),
            # parameter_95
            paddle.static.InputSpec(shape=[120], dtype='float32'),
            # parameter_98
            paddle.static.InputSpec(shape=[30, 120, 1, 1], dtype='float32'),
            # parameter_99
            paddle.static.InputSpec(shape=[30], dtype='float32'),
            # parameter_100
            paddle.static.InputSpec(shape=[120, 30, 1, 1], dtype='float32'),
            # parameter_101
            paddle.static.InputSpec(shape=[120], dtype='float32'),
            # parameter_102
            paddle.static.InputSpec(shape=[40, 120, 1, 1], dtype='float32'),
            # parameter_106
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_103
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_105
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_104
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_107
            paddle.static.InputSpec(shape=[240, 40, 1, 1], dtype='float32'),
            # parameter_111
            paddle.static.InputSpec(shape=[240], dtype='float32'),
            # parameter_108
            paddle.static.InputSpec(shape=[240], dtype='float32'),
            # parameter_110
            paddle.static.InputSpec(shape=[240], dtype='float32'),
            # parameter_109
            paddle.static.InputSpec(shape=[240], dtype='float32'),
            # parameter_112
            paddle.static.InputSpec(shape=[240, 1, 3, 3], dtype='float32'),
            # parameter_116
            paddle.static.InputSpec(shape=[240], dtype='float32'),
            # parameter_113
            paddle.static.InputSpec(shape=[240], dtype='float32'),
            # parameter_115
            paddle.static.InputSpec(shape=[240], dtype='float32'),
            # parameter_114
            paddle.static.InputSpec(shape=[240], dtype='float32'),
            # parameter_117
            paddle.static.InputSpec(shape=[80, 240, 1, 1], dtype='float32'),
            # parameter_121
            paddle.static.InputSpec(shape=[80], dtype='float32'),
            # parameter_118
            paddle.static.InputSpec(shape=[80], dtype='float32'),
            # parameter_120
            paddle.static.InputSpec(shape=[80], dtype='float32'),
            # parameter_119
            paddle.static.InputSpec(shape=[80], dtype='float32'),
            # parameter_122
            paddle.static.InputSpec(shape=[200, 80, 1, 1], dtype='float32'),
            # parameter_126
            paddle.static.InputSpec(shape=[200], dtype='float32'),
            # parameter_123
            paddle.static.InputSpec(shape=[200], dtype='float32'),
            # parameter_125
            paddle.static.InputSpec(shape=[200], dtype='float32'),
            # parameter_124
            paddle.static.InputSpec(shape=[200], dtype='float32'),
            # parameter_127
            paddle.static.InputSpec(shape=[200, 1, 3, 3], dtype='float32'),
            # parameter_131
            paddle.static.InputSpec(shape=[200], dtype='float32'),
            # parameter_128
            paddle.static.InputSpec(shape=[200], dtype='float32'),
            # parameter_130
            paddle.static.InputSpec(shape=[200], dtype='float32'),
            # parameter_129
            paddle.static.InputSpec(shape=[200], dtype='float32'),
            # parameter_132
            paddle.static.InputSpec(shape=[80, 200, 1, 1], dtype='float32'),
            # parameter_136
            paddle.static.InputSpec(shape=[80], dtype='float32'),
            # parameter_133
            paddle.static.InputSpec(shape=[80], dtype='float32'),
            # parameter_135
            paddle.static.InputSpec(shape=[80], dtype='float32'),
            # parameter_134
            paddle.static.InputSpec(shape=[80], dtype='float32'),
            # parameter_137
            paddle.static.InputSpec(shape=[184, 80, 1, 1], dtype='float32'),
            # parameter_141
            paddle.static.InputSpec(shape=[184], dtype='float32'),
            # parameter_138
            paddle.static.InputSpec(shape=[184], dtype='float32'),
            # parameter_140
            paddle.static.InputSpec(shape=[184], dtype='float32'),
            # parameter_139
            paddle.static.InputSpec(shape=[184], dtype='float32'),
            # parameter_142
            paddle.static.InputSpec(shape=[184, 1, 3, 3], dtype='float32'),
            # parameter_146
            paddle.static.InputSpec(shape=[184], dtype='float32'),
            # parameter_143
            paddle.static.InputSpec(shape=[184], dtype='float32'),
            # parameter_145
            paddle.static.InputSpec(shape=[184], dtype='float32'),
            # parameter_144
            paddle.static.InputSpec(shape=[184], dtype='float32'),
            # parameter_147
            paddle.static.InputSpec(shape=[80, 184, 1, 1], dtype='float32'),
            # parameter_151
            paddle.static.InputSpec(shape=[80], dtype='float32'),
            # parameter_148
            paddle.static.InputSpec(shape=[80], dtype='float32'),
            # parameter_150
            paddle.static.InputSpec(shape=[80], dtype='float32'),
            # parameter_149
            paddle.static.InputSpec(shape=[80], dtype='float32'),
            # parameter_152
            paddle.static.InputSpec(shape=[184, 80, 1, 1], dtype='float32'),
            # parameter_156
            paddle.static.InputSpec(shape=[184], dtype='float32'),
            # parameter_153
            paddle.static.InputSpec(shape=[184], dtype='float32'),
            # parameter_155
            paddle.static.InputSpec(shape=[184], dtype='float32'),
            # parameter_154
            paddle.static.InputSpec(shape=[184], dtype='float32'),
            # parameter_157
            paddle.static.InputSpec(shape=[184, 1, 3, 3], dtype='float32'),
            # parameter_161
            paddle.static.InputSpec(shape=[184], dtype='float32'),
            # parameter_158
            paddle.static.InputSpec(shape=[184], dtype='float32'),
            # parameter_160
            paddle.static.InputSpec(shape=[184], dtype='float32'),
            # parameter_159
            paddle.static.InputSpec(shape=[184], dtype='float32'),
            # parameter_162
            paddle.static.InputSpec(shape=[80, 184, 1, 1], dtype='float32'),
            # parameter_166
            paddle.static.InputSpec(shape=[80], dtype='float32'),
            # parameter_163
            paddle.static.InputSpec(shape=[80], dtype='float32'),
            # parameter_165
            paddle.static.InputSpec(shape=[80], dtype='float32'),
            # parameter_164
            paddle.static.InputSpec(shape=[80], dtype='float32'),
            # parameter_167
            paddle.static.InputSpec(shape=[480, 80, 1, 1], dtype='float32'),
            # parameter_171
            paddle.static.InputSpec(shape=[480], dtype='float32'),
            # parameter_168
            paddle.static.InputSpec(shape=[480], dtype='float32'),
            # parameter_170
            paddle.static.InputSpec(shape=[480], dtype='float32'),
            # parameter_169
            paddle.static.InputSpec(shape=[480], dtype='float32'),
            # parameter_172
            paddle.static.InputSpec(shape=[480, 1, 3, 3], dtype='float32'),
            # parameter_176
            paddle.static.InputSpec(shape=[480], dtype='float32'),
            # parameter_173
            paddle.static.InputSpec(shape=[480], dtype='float32'),
            # parameter_175
            paddle.static.InputSpec(shape=[480], dtype='float32'),
            # parameter_174
            paddle.static.InputSpec(shape=[480], dtype='float32'),
            # parameter_177
            paddle.static.InputSpec(shape=[120, 480, 1, 1], dtype='float32'),
            # parameter_178
            paddle.static.InputSpec(shape=[120], dtype='float32'),
            # parameter_179
            paddle.static.InputSpec(shape=[480, 120, 1, 1], dtype='float32'),
            # parameter_180
            paddle.static.InputSpec(shape=[480], dtype='float32'),
            # parameter_181
            paddle.static.InputSpec(shape=[112, 480, 1, 1], dtype='float32'),
            # parameter_185
            paddle.static.InputSpec(shape=[112], dtype='float32'),
            # parameter_182
            paddle.static.InputSpec(shape=[112], dtype='float32'),
            # parameter_184
            paddle.static.InputSpec(shape=[112], dtype='float32'),
            # parameter_183
            paddle.static.InputSpec(shape=[112], dtype='float32'),
            # parameter_186
            paddle.static.InputSpec(shape=[672, 112, 1, 1], dtype='float32'),
            # parameter_190
            paddle.static.InputSpec(shape=[672], dtype='float32'),
            # parameter_187
            paddle.static.InputSpec(shape=[672], dtype='float32'),
            # parameter_189
            paddle.static.InputSpec(shape=[672], dtype='float32'),
            # parameter_188
            paddle.static.InputSpec(shape=[672], dtype='float32'),
            # parameter_191
            paddle.static.InputSpec(shape=[672, 1, 3, 3], dtype='float32'),
            # parameter_195
            paddle.static.InputSpec(shape=[672], dtype='float32'),
            # parameter_192
            paddle.static.InputSpec(shape=[672], dtype='float32'),
            # parameter_194
            paddle.static.InputSpec(shape=[672], dtype='float32'),
            # parameter_193
            paddle.static.InputSpec(shape=[672], dtype='float32'),
            # parameter_196
            paddle.static.InputSpec(shape=[168, 672, 1, 1], dtype='float32'),
            # parameter_197
            paddle.static.InputSpec(shape=[168], dtype='float32'),
            # parameter_198
            paddle.static.InputSpec(shape=[672, 168, 1, 1], dtype='float32'),
            # parameter_199
            paddle.static.InputSpec(shape=[672], dtype='float32'),
            # parameter_200
            paddle.static.InputSpec(shape=[112, 672, 1, 1], dtype='float32'),
            # parameter_204
            paddle.static.InputSpec(shape=[112], dtype='float32'),
            # parameter_201
            paddle.static.InputSpec(shape=[112], dtype='float32'),
            # parameter_203
            paddle.static.InputSpec(shape=[112], dtype='float32'),
            # parameter_202
            paddle.static.InputSpec(shape=[112], dtype='float32'),
            # parameter_205
            paddle.static.InputSpec(shape=[672, 112, 1, 1], dtype='float32'),
            # parameter_209
            paddle.static.InputSpec(shape=[672], dtype='float32'),
            # parameter_206
            paddle.static.InputSpec(shape=[672], dtype='float32'),
            # parameter_208
            paddle.static.InputSpec(shape=[672], dtype='float32'),
            # parameter_207
            paddle.static.InputSpec(shape=[672], dtype='float32'),
            # parameter_210
            paddle.static.InputSpec(shape=[672, 1, 5, 5], dtype='float32'),
            # parameter_214
            paddle.static.InputSpec(shape=[672], dtype='float32'),
            # parameter_211
            paddle.static.InputSpec(shape=[672], dtype='float32'),
            # parameter_213
            paddle.static.InputSpec(shape=[672], dtype='float32'),
            # parameter_212
            paddle.static.InputSpec(shape=[672], dtype='float32'),
            # parameter_215
            paddle.static.InputSpec(shape=[168, 672, 1, 1], dtype='float32'),
            # parameter_216
            paddle.static.InputSpec(shape=[168], dtype='float32'),
            # parameter_217
            paddle.static.InputSpec(shape=[672, 168, 1, 1], dtype='float32'),
            # parameter_218
            paddle.static.InputSpec(shape=[672], dtype='float32'),
            # parameter_219
            paddle.static.InputSpec(shape=[160, 672, 1, 1], dtype='float32'),
            # parameter_223
            paddle.static.InputSpec(shape=[160], dtype='float32'),
            # parameter_220
            paddle.static.InputSpec(shape=[160], dtype='float32'),
            # parameter_222
            paddle.static.InputSpec(shape=[160], dtype='float32'),
            # parameter_221
            paddle.static.InputSpec(shape=[160], dtype='float32'),
            # parameter_224
            paddle.static.InputSpec(shape=[960, 160, 1, 1], dtype='float32'),
            # parameter_228
            paddle.static.InputSpec(shape=[960], dtype='float32'),
            # parameter_225
            paddle.static.InputSpec(shape=[960], dtype='float32'),
            # parameter_227
            paddle.static.InputSpec(shape=[960], dtype='float32'),
            # parameter_226
            paddle.static.InputSpec(shape=[960], dtype='float32'),
            # parameter_229
            paddle.static.InputSpec(shape=[960, 1, 5, 5], dtype='float32'),
            # parameter_233
            paddle.static.InputSpec(shape=[960], dtype='float32'),
            # parameter_230
            paddle.static.InputSpec(shape=[960], dtype='float32'),
            # parameter_232
            paddle.static.InputSpec(shape=[960], dtype='float32'),
            # parameter_231
            paddle.static.InputSpec(shape=[960], dtype='float32'),
            # parameter_234
            paddle.static.InputSpec(shape=[240, 960, 1, 1], dtype='float32'),
            # parameter_235
            paddle.static.InputSpec(shape=[240], dtype='float32'),
            # parameter_236
            paddle.static.InputSpec(shape=[960, 240, 1, 1], dtype='float32'),
            # parameter_237
            paddle.static.InputSpec(shape=[960], dtype='float32'),
            # parameter_238
            paddle.static.InputSpec(shape=[160, 960, 1, 1], dtype='float32'),
            # parameter_242
            paddle.static.InputSpec(shape=[160], dtype='float32'),
            # parameter_239
            paddle.static.InputSpec(shape=[160], dtype='float32'),
            # parameter_241
            paddle.static.InputSpec(shape=[160], dtype='float32'),
            # parameter_240
            paddle.static.InputSpec(shape=[160], dtype='float32'),
            # parameter_243
            paddle.static.InputSpec(shape=[960, 160, 1, 1], dtype='float32'),
            # parameter_247
            paddle.static.InputSpec(shape=[960], dtype='float32'),
            # parameter_244
            paddle.static.InputSpec(shape=[960], dtype='float32'),
            # parameter_246
            paddle.static.InputSpec(shape=[960], dtype='float32'),
            # parameter_245
            paddle.static.InputSpec(shape=[960], dtype='float32'),
            # parameter_248
            paddle.static.InputSpec(shape=[960, 1, 5, 5], dtype='float32'),
            # parameter_252
            paddle.static.InputSpec(shape=[960], dtype='float32'),
            # parameter_249
            paddle.static.InputSpec(shape=[960], dtype='float32'),
            # parameter_251
            paddle.static.InputSpec(shape=[960], dtype='float32'),
            # parameter_250
            paddle.static.InputSpec(shape=[960], dtype='float32'),
            # parameter_253
            paddle.static.InputSpec(shape=[240, 960, 1, 1], dtype='float32'),
            # parameter_254
            paddle.static.InputSpec(shape=[240], dtype='float32'),
            # parameter_255
            paddle.static.InputSpec(shape=[960, 240, 1, 1], dtype='float32'),
            # parameter_256
            paddle.static.InputSpec(shape=[960], dtype='float32'),
            # parameter_257
            paddle.static.InputSpec(shape=[160, 960, 1, 1], dtype='float32'),
            # parameter_261
            paddle.static.InputSpec(shape=[160], dtype='float32'),
            # parameter_258
            paddle.static.InputSpec(shape=[160], dtype='float32'),
            # parameter_260
            paddle.static.InputSpec(shape=[160], dtype='float32'),
            # parameter_259
            paddle.static.InputSpec(shape=[160], dtype='float32'),
            # parameter_262
            paddle.static.InputSpec(shape=[128, 160, 1, 1], dtype='float32'),
            # parameter_263
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_267
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_264
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_266
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_265
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_268
            paddle.static.InputSpec(shape=[128, 160, 1, 1], dtype='float32'),
            # parameter_269
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_273
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_270
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_272
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_271
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_274
            paddle.static.InputSpec(shape=[128, 160, 1, 1], dtype='float32'),
            # parameter_275
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_279
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_276
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_278
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_277
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_280
            paddle.static.InputSpec(shape=[128, 1, 3, 3], dtype='float32'),
            # parameter_284
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_281
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_283
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_282
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_285
            paddle.static.InputSpec(shape=[128, 128, 1, 1], dtype='float32'),
            # parameter_286
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_290
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_287
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_289
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_288
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_291
            paddle.static.InputSpec(shape=[160, 1, 3, 3], dtype='float32'),
            # parameter_295
            paddle.static.InputSpec(shape=[160], dtype='float32'),
            # parameter_292
            paddle.static.InputSpec(shape=[160], dtype='float32'),
            # parameter_294
            paddle.static.InputSpec(shape=[160], dtype='float32'),
            # parameter_293
            paddle.static.InputSpec(shape=[160], dtype='float32'),
            # parameter_296
            paddle.static.InputSpec(shape=[128, 160, 1, 1], dtype='float32'),
            # parameter_297
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_301
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_298
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_300
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_299
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_302
            paddle.static.InputSpec(shape=[128, 1, 3, 3], dtype='float32'),
            # parameter_306
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_303
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_305
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_304
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_307
            paddle.static.InputSpec(shape=[128, 128, 1, 1], dtype='float32'),
            # parameter_308
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_312
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_309
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_311
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_310
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_313
            paddle.static.InputSpec(shape=[112, 1, 3, 3], dtype='float32'),
            # parameter_317
            paddle.static.InputSpec(shape=[112], dtype='float32'),
            # parameter_314
            paddle.static.InputSpec(shape=[112], dtype='float32'),
            # parameter_316
            paddle.static.InputSpec(shape=[112], dtype='float32'),
            # parameter_315
            paddle.static.InputSpec(shape=[112], dtype='float32'),
            # parameter_318
            paddle.static.InputSpec(shape=[128, 112, 1, 1], dtype='float32'),
            # parameter_319
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_323
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_320
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_322
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_321
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_324
            paddle.static.InputSpec(shape=[128, 1, 3, 3], dtype='float32'),
            # parameter_328
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_325
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_327
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_326
            paddle.static.InputSpec(shape=[128], dtype='float32'),
            # parameter_329
            paddle.static.InputSpec(shape=[64, 128, 1, 1], dtype='float32'),
            # parameter_330
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_334
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_331
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_333
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_332
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_335
            paddle.static.InputSpec(shape=[40, 1, 3, 3], dtype='float32'),
            # parameter_339
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_336
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_338
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_337
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_340
            paddle.static.InputSpec(shape=[64, 40, 1, 1], dtype='float32'),
            # parameter_341
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_345
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_342
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_344
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_343
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_346
            paddle.static.InputSpec(shape=[64, 1, 3, 3], dtype='float32'),
            # parameter_350
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_347
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_349
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_348
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_351
            paddle.static.InputSpec(shape=[32, 64, 1, 1], dtype='float32'),
            # parameter_352
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_356
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_353
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_355
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_354
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_357
            paddle.static.InputSpec(shape=[32, 1, 3, 3], dtype='float32'),
            # parameter_361
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_358
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_360
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_359
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_362
            paddle.static.InputSpec(shape=[32, 32, 1, 1], dtype='float32'),
            # parameter_363
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_367
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_364
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_366
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_365
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_368
            paddle.static.InputSpec(shape=[19, 32, 1, 1], dtype='float32'),
            # feed_0
            paddle.static.InputSpec(shape=[None, 3, None, None], dtype='float32'),
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