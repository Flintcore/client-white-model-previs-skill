"""Actual FFmpeg timestamp probes; tiny synthetic media, not client approval."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS=Path(__file__).resolve().parents[1]/'skills'/'client-white-model-previs'/'scripts'
sys.path.insert(0,str(SCRIPTS))
from common import video_info


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'Actual FFmpeg and FFprobe required')
class MediaTimingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='previs-media-timing-')
        self.root=Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def create(self,rate='24',vfr=False):
        path=self.root/'source.mp4'
        command=['ffmpeg','-hide_banner','-v','error','-y','-f','lavfi','-i',f'color=c=gray:s=64x64:r={rate}',
                 '-frames:v','8']
        if vfr:
            command+=['-vf',r'setpts=if(lt(N\,4)\,N\,N+3)/(24*TB)','-fps_mode','vfr']
        command+=['-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',str(path)]
        result=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        return path

    def test_actual_cfr_frames_and_fraction(self):
        info=video_info(self.create())
        self.assertEqual((info['fps_num'],info['fps_den'],info['frame_count']),(24,1,8))

    def test_actual_ntsc_fraction_is_preserved(self):
        info=video_info(self.create('30000/1001'))
        self.assertEqual((info['fps_num'],info['fps_den']),(30000,1001))

    def test_average_rate_does_not_certify_variable_frame_timing(self):
        with self.assertRaisesRegex(ValueError,'Variable/discontinuous'):
            video_info(self.create(vfr=True))


if __name__=='__main__':unittest.main()
