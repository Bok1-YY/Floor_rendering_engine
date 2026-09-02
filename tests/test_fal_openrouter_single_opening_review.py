import base64,json
from pathlib import Path
import subprocess,sys
from tools.goal_loop_v2.fal_openrouter_single_opening_review import MODEL,build_request
def test_request_uses_two_bound_images_and_billing_safe_single_model(tmp_path):
    full=tmp_path/'full.png';crop=tmp_path/'crop.png';full.write_bytes(b'full');crop.write_bytes(b'crop');payload,prompt,bindings=build_request('OP006',full,crop)
    assert payload['model']==MODEL=='google/gemini-2.5-flash'
    assert len(payload['image_urls'])==2 and all(x.startswith('data:image/png;base64,') for x in payload['image_urls'])
    assert base64.b64decode(payload['image_urls'][0].split(',',1)[1])==b'full'
    assert payload['temperature']==0 and payload['reasoning'] is False and payload['enable_web_search'] is False
    assert 'Review ONLY opening OP006' in prompt and [x['bytes'] for x in bindings]==[4,4]
def test_direct_script_imports_from_outside_repository(tmp_path):
    root=Path(__file__).resolve().parents[1];script=root/'tools/goal_loop_v2/fal_openrouter_single_opening_review.py';result=subprocess.run([sys.executable,str(script),'--help'],cwd=tmp_path,text=True,capture_output=True,check=False);assert result.returncode==0,result.stderr;assert 'fal OpenRouter Vision' in result.stdout
