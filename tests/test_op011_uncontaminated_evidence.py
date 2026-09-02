import json,hashlib,subprocess,sys
from pathlib import Path
from PIL import Image
from tools.goal_loop_v2.build_op011_uncontaminated_evidence import build
ROOT=Path(__file__).resolve().parents[1]
def test_raw_crop_matches_source_region_and_locator_is_clear():
 c=build();raw=Image.open(ROOT/'data/goal_loop_v2/references/1308/canonical-raw-portrait.png');box=tuple(c['crop_box_px']);crop=Image.open(c['artifacts']['raw_crop']['path']);expected=raw.crop(box);assert (crop.mode,crop.size,crop.tobytes())==(expected.mode,expected.size,expected.tobytes());assert c['source_pixels_untouched'] is True;assert c['registration']['max_endpoint_error_px']<=1;assert c['locator_min_clearance_px']>=20;assert all(Path(v['path']).exists() and len(v['sha256'])==64 for v in c['artifacts'].values())
def test_direct_script_help_from_temp_cwd(tmp_path):
 s=ROOT/'tools/goal_loop_v2/build_op011_uncontaminated_evidence.py';r=subprocess.run([sys.executable,str(s),'--help'],cwd=tmp_path,capture_output=True,text=True);assert r.returncode==0
