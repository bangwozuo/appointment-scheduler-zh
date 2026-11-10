import unittest,importlib.util,json
from pathlib import Path
from generated_agent.domain import execute
class SourceBoundaries(unittest.TestCase):
 def test_missing(self):self.assertEqual(execute({'operation':'commission','data':{},'data_origin':'SYNTHETIC'})['status'],'needs_input')
 def test_wrong_source(self):
  with self.assertRaises(ValueError):execute({'operation':'commission','data':{},'data_origin':'REAL'})
 def test_unknown_operation(self):
  with self.assertRaises(ValueError):execute({'operation':'../secret','data':{},'data_origin':'SYNTHETIC'})
 def test_original_sources_compile(self):
  files=list((Path(__file__).resolve().parent.parent/'repo').rglob('*.py'));self.assertEqual(len(files),8)
  for f in files:compile(f.read_text('utf-8'),str(f),'exec')
 def test_money_half_up_and_source_example(self):
  p=Path(__file__).resolve().parent.parent/'repo/skills/commission-calc-assist/scripts/commission_calc.py';s=importlib.util.spec_from_file_location('commission',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
  self.assertEqual(m.calc(m.demo_data())['total'],1013.2)
  self.assertEqual(m.calc({'items':[{'category':'x','unit_price':0.05,'count':1}], 'rules':{'category_rates':{'x':0.3},'channel_deduction':{}}})['total'],0.02)
