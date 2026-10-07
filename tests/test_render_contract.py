"""Current render specification and amendment provenance, not visual approval."""
import copy,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/client-white-model-previs/scripts'))
import common

class RevisedRenderContractTests(unittest.TestCase):
    def setUp(self):
        self.source={'width':3840,'height':2160}
        self.render={'width':3840,'height':2160,'percentage':100,'samples':64,
                     'dark_scene':True,'raytracing':False,'output_width':1920,'output_height':1080}
        self.profile='client-4k-project-1080p'
    def test_project_and_output_pixels_are_separate(self):
        common.validate_render_contract(self.render,self.source,self.profile)
        self.assertEqual(common.delivery_dimensions(self.render),(1920,1080))
        self.assertTrue(common.raytracing_matches(self.render,False,self.profile))
    def test_raytracing_is_disabled_including_dark_scenes(self):
        for saved in [True,None,0,'false']:
            self.assertFalse(common.raytracing_matches(self.render,saved,self.profile))
    def test_wrong_project_or_output_or_samples_are_rejected(self):
        for key,value in [('width',1920),('height',1080),('output_width',3840),
                          ('output_height',2160),('samples',32),('percentage',50),('raytracing',True)]:
            with self.subTest(key=key):
                render=copy.deepcopy(self.render);render[key]=value
                with self.assertRaises(ValueError):common.validate_render_contract(render,self.source,self.profile)
    def test_lower_resolution_source_keeps_aspect_without_claiming_original_4k(self):
        common.validate_render_contract(self.render,{'width':1920,'height':1080},self.profile)
    def test_aspect_change_is_not_a_silent_crop(self):
        with self.assertRaisesRegex(ValueError,'aspect'):
            common.validate_render_contract(self.render,{'width':1440,'height':1080},self.profile)
    def test_portrait_spec_is_explicit(self):
        render=copy.deepcopy(self.render)
        render.update(width=2160,height=3840,output_width=1080,output_height=1920)
        common.validate_render_contract(render,{'width':2160,'height':3840},self.profile)
    def test_amendment_preserves_rule_mapping_and_123_checks(self):
        data=common.rules();rules={r['id']:r for r in data['rules']}
        self.assertEqual(data['version'],'1.1.0')
        self.assertEqual(len(rules),133);self.assertEqual(len(common.blocking_rules()),123)
        self.assertEqual(set(data['amendments'][-1]['affected_rules']),{'CW071','CW074'})
        for key in ['CW071','CW074']:
            self.assertTrue(rules[key]['blocking'])
            self.assertTrue(rules[key]['superseded_requirement'])
            self.assertEqual(rules[key]['amendment_id'],'A20261007_RENDER')

if __name__=='__main__':unittest.main()
