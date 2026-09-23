"""Convert local HF Whisper safetensors to MLX FP16, without executing model code.

Key mapping follows Apple's MIT-licensed mlx-examples/whisper/convert.py:
https://github.com/ml-explore/mlx-examples/blob/main/whisper/convert.py
Copyright © 2023-2024 Apple Inc. Mapping adapted under MIT; see
third_party/MLX_EXAMPLES_LICENSE.txt.
Supports sharded checkpoints. Does not download or modify the source.
"""
import argparse
import json
from pathlib import Path
import mlx.core as mx
from mlx.utils import tree_flatten
from mlx_whisper.whisper import ModelDimensions, Whisper


def convert(source, output):
    if output.exists():
        raise ValueError('Output already exists; choose a new directory')
    c = json.loads((source / 'config.json').read_text())
    dims = dict(n_mels=c['num_mel_bins'], n_audio_ctx=c['max_source_positions'],
                n_audio_state=c['d_model'], n_audio_head=c['encoder_attention_heads'],
                n_audio_layer=c['encoder_layers'], n_vocab=c['vocab_size'],
                n_text_ctx=c['max_target_positions'], n_text_state=c['d_model'],
                n_text_head=c['decoder_attention_heads'], n_text_layer=c['decoder_layers'])
    index = source / 'model.safetensors.index.json'
    shards = sorted(set(json.loads(index.read_text())['weight_map'].values())) if index.exists() else ['model.safetensors']
    pairs = [('model.', ''), ('.layers', '.blocks'), ('.self_attn', '.attn'),
             ('.attn_layer_norm', '.attn_ln'), ('.encoder_attn.', '.cross_attn.'),
             ('.encoder_attn_layer_norm', '.cross_attn_ln'), ('.final_layer_norm', '.mlp_ln'),
             ('.q_proj', '.query'), ('.k_proj', '.key'), ('.v_proj', '.value'),
             ('.out_proj', '.out'), ('.fc1', '.mlp1'), ('.fc2', '.mlp2'),
             ('embed_positions.weight', 'positional_embedding'),
             ('decoder.embed_tokens', 'decoder.token_embedding'),
             ('encoder.layer_norm', 'encoder.ln_post'), ('decoder.layer_norm', 'decoder.ln')]
    weights = {}
    for shard in shards:
        raw = mx.load(str(source / shard))
        for key, value in raw.items():
            if key == 'proj_out.weight':
                continue
            for before, after in pairs:
                key = key.replace(before, after)
            if key == 'encoder.positional_embedding':
                continue
            if 'conv' in key and value.ndim == 3:
                value = value.swapaxes(1, 2)
            if key in weights:
                raise ValueError('Duplicate parameter: ' + key)
            weights[key] = value.astype(mx.float16)
            mx.eval(weights[key])
        del raw
        print('Converted shard:', shard, flush=True)
    model = Whisper(ModelDimensions(**dims), mx.float16)
    weights['alignment_heads'] = model.alignment_heads
    expected = dict(tree_flatten(model.parameters()))
    if set(weights) != set(expected):
        raise ValueError(f'Parameter mismatch: missing={set(expected)-set(weights)}, extra={set(weights)-set(expected)}')
    for key in weights:
        if weights[key].shape != expected[key].shape:
            raise ValueError('Shape mismatch: ' + key)
    output.mkdir(parents=True)
    mx.save_safetensors(str(output / 'weights.safetensors'), weights)
    (output / 'config.json').write_text(json.dumps(dims, indent=2))
    (output / 'conversion.json').write_text(json.dumps(dict(source=str(source), dtype='float16',
        alignment='MLX default heads; fine-tuned word alignment not separately calibrated'), indent=2))
    print('Saved:', output, flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('output', type=Path)
    args = p.parse_args()
    convert(args.source.resolve(), args.output.resolve())
