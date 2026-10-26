"""Existing server model, CPU-only real inference and identical numeric arms."""
from pathlib import Path
from datetime import datetime,timezone
import os,sys,json,hashlib,csv,subprocess,time,gc
os.environ.update(CUDA_VISIBLE_DEVICES='',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',OMP_NUM_THREADS='4',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='4')
ROOT=Path(__file__).resolve().parent
import run_frozen_llm_validation_20261023 as base
import numpy as np

def locate_model():
    for current,dirs,files in os.walk(Path.home()):
        dirs[:]=[d for d in dirs if d not in ['.git','.ssh','node_modules','site-packages','.secrets','secrets']]
        if Path(current).name=='Qwen3-4B-Instruct-2507' and 'config.json' in files:return Path(current)
    raise RuntimeError('existing model missing')

class ExistingModel:
    def __init__(self,path):
        import torch,transformers
        from transformers import AutoTokenizer,AutoModelForCausalLM
        transformers.utils.logging.set_verbosity_error();transformers.utils.logging.disable_progress_bar()
        torch.set_num_threads(4);torch.set_num_interop_threads(1);torch.manual_seed(20261023)
        self.torch=torch
        self.tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True,trust_remote_code=False)
        self.model=AutoModelForCausalLM.from_pretrained(path,torch_dtype=torch.float32,local_files_only=True,trust_remote_code=False).eval()
    def create_chat_completion(self,*,messages,temperature,max_tokens,response_format,seed):
        self.torch.manual_seed(seed)
        text=self.tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True)
        inputs=self.tokenizer(text,return_tensors='pt');n=inputs['input_ids'].shape[1]
        with self.torch.inference_mode():output=self.model.generate(**inputs,max_new_tokens=max_tokens,do_sample=False,use_cache=True,pad_token_id=self.tokenizer.eos_token_id)
        new=output[0,n:];content=self.tokenizer.decode(new,skip_special_tokens=True)
        return {'choices':[{'message':{'content':content},'finish_reason':'length' if len(new)==max_tokens else 'stop'}],
          'usage':{'prompt_tokens':n,'completion_tokens':len(new),'total_tokens':n+len(new)}}

def main():
    base.status('existing_qwen3_initializing')
    path=locate_model()
    if not (ROOT/'protocol_v2_existing_model.json').exists():
        original=json.loads((ROOT/'protocol.json').read_text())
        original.update(version='frozen-v2-existing-model',amended_at=base.now(),amendment_reason='Complete host audit found original Qwen3 weights and compatible Conda environment; no model inference or prediction metrics from v1 had been produced; replace unneeded download with existing server model',
          original_protocol_preserved=True,changes_affecting_data_window_or_candidate_rules=False,
          llm={'name':path.name,'backend':'transformers','precision':'float32','device':'cpu','threads':4,'temperature':0,'seed':20261023,'max_output_tokens':96,'calls_per_task':1,'response_format':'unconstrained actual generated text, strict JSON parser; invalid output falls back; not API tool_calls'},
          pretraining_limitation='Existing model training sources may include public historical data; prompts omit disease, country, year, dates and future labels; no claim of pretraining-blind test',
          code_sha256={n:base.sha(ROOT/n) for n in ['run_existing_qwen3_validation_20261023.py','run_frozen_llm_validation_20261023.py','frozen_llm_validation_core_20261023.py']})
        base.write('protocol_v2_existing_model.json',original)
    with (ROOT/'unit_tests_existing_runtime.log').open('w') as f:r=subprocess.run([sys.executable,str(ROOT/'test_frozen_llm_validation_20261023.py'),'-v'],stdout=f,stderr=f)
    if r.returncode:raise RuntimeError('existing-runtime unit tests failed')
    if not (ROOT/'existing_model_fingerprints.json').exists():
        import importlib.metadata
        base.write('existing_model_fingerprints.json',{'name':path.name,'files':{p.name:base.sha(p) for p in path.iterdir() if p.is_file() and p.name.endswith(('.safetensors','.json'))},
          'runtime':{k:importlib.metadata.version(k) for k in ['torch','transformers','safetensors','numpy']},'CPU_only':True,'trust_remote_code':False,'threads':4})
    arrays=base.source_arrays();new={}
    with (ROOT/'new_window_observations.csv').open() as f:observations=list(csv.DictReader(f))
    for disease,(weeks,y) in arrays.items():
        extra=sorted([r for r in observations if r['disease']==disease],key=lambda r:r['week'])
        assert len(extra)==104 and [r['week'] for r in extra]==[f'{year}-W{w:02d}' for year in [2023,2024] for w in range(1,53)]
        new[disease]=(weeks+[r['week'] for r in extra],np.r_[y,[float(r['cases']) for r in extra]])
    base.status('existing_qwen3_loading');start=time.perf_counter();llm=ExistingModel(path)
    base.write('existing_model_load.json',{'seconds':time.perf_counter()-start,'device':'cpu','threads':4})
    proposals=base.proposals(llm,arrays,[2020,2021,2022]);new_proposals=base.proposals(llm,new,[2024]);del llm;gc.collect()
    base.evaluate(arrays,proposals,[2020,2021,2022],'historical_llm')
    base.evaluate(new,new_proposals,[2024],'new_window')
    base.write('result_summary.json',{'historical_llm_ablation_completed':True,'new_window_completed':True,'real_llm_calls':len(proposals)+len(new_proposals),'model':'Qwen3-4B-Instruct-2507','all_execution_on_vm201':True,'review_status':'pending human analyst review','identity':'historical holdout after pipeline freeze; not prospective or independently custodied blind test'})
    base.status('completed',real_llm_calls=len(proposals)+len(new_proposals))

if __name__=='__main__':
    try:main()
    except Exception as e:
        base.status('failed_existing_model',error_type=type(e).__name__)
        (ROOT/'existing_model_failure_type.json').write_text(json.dumps({'error_type':type(e).__name__}))
