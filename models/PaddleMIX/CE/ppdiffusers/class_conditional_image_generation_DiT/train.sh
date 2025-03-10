#!/bin/bash

TRAINING_MODEL_RESUME="None"
TRAINER_INSTANCES='127.0.0.1'
MASTER='127.0.0.1:8080'
TRAINERS_NUM=1           # nnodes, machine num
TRAINING_GPUS_PER_NODE=2 # nproc_per_node
DP_DEGREE=1              # dp_parallel_degree
MP_DEGREE=1              # tensor_parallel_degree
SHARDING_DEGREE=1        # sharding_parallel_degree

config_file=config/DiT_XL_patch2.json
OUTPUT_DIR=./output_trainer/DiT_XL_patch2_trainer

feature_path=./fastdit_imagenet256_tiny
batch_size=2 # per gpu
num_workers=2
max_steps=50
logging_steps=10
save_steps=50
image_logging_steps=-1
seed=0

USE_AMP=True
FP16_OPT_LEVEL="O1"
enable_tensorboard=True
recompute=True
enable_xformers=True

TRAINING_PYTHON="python -m paddle.distributed.launch --master ${MASTER} --nnodes ${TRAINERS_NUM} --nproc_per_node ${TRAINING_GPUS_PER_NODE} --ips ${TRAINER_INSTANCES}"
${TRAINING_PYTHON} train_image_generation_trainer.py \
    --do_train \
    --feature_path ${feature_path} \
    --output_dir ${OUTPUT_DIR} \
    --per_device_train_batch_size ${batch_size} \
    --gradient_accumulation_steps 1 \
    --learning_rate 1e-4 \
    --weight_decay 0.0 \
    --max_steps ${max_steps} \
    --lr_scheduler_type "constant" \
    --warmup_steps 0 \
    --image_logging_steps ${image_logging_steps} \
    --logging_dir ${OUTPUT_DIR}/tb_log \
    --logging_steps ${logging_steps} \
    --save_steps ${save_steps} \
    --save_total_limit 50 \
    --dataloader_num_workers ${num_workers} \
    --vae_name_or_path stabilityai/sd-vae-ft-mse \
    --config_file ${config_file} \
    --num_inference_steps 25 \
    --use_ema True \
    --max_grad_norm -1 \
    --overwrite_output_dir True \
    --disable_tqdm True \
    --fp16_opt_level ${FP16_OPT_LEVEL} \
    --seed ${seed} \
    --recompute ${recompute} \
    --enable_xformers_memory_efficient_attention ${enable_xformers} \
    --bf16 ${USE_AMP} \
    --dp_degree ${DP_DEGREE} \
    --tensor_parallel_degree ${MP_DEGREE} \
    --sharding_parallel_degree ${SHARDING_DEGREE} \
    --pipeline_parallel_degree 1 \
    --sep_parallel_degree 1

#!/bin/bash
TARGET_DIR="./"
FILES=$(find "$TARGET_DIR" -type f -name "test_*.py")
if [ -z "$FILES" ]; then
    echo "没有找到以test_开头的文件。"
    exit 1
for FILE in $FILES; do
    echo "正在执行测试文件：$FILE"
    pytest "$FILE"
    if [ $? -ne 0 ]; then
        echo "测试文件 $FILE 执行失败。"
    fi
done
