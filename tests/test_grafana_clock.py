"""Grafana's page-local clock preserves explicit dates and the parent clock."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GrafanaClockTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is required for browser JavaScript checks")
    def test_page_clock_relative_time_without_changing_explicit_dates(self):
        harness = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
for(const offset of [2612000,-2612000]){
 const context=vm.createContext({location:{pathname:'/grafana/d/fixture/'},document:{currentScript:{dataset:{}}},tick:5000});
 vm.runInContext('window=globalThis;OriginalDate=Date;originalNow=Date.now();parent={Date};performance={now:()=>tick};serverNow=originalNow+'+offset+';document.currentScript.dataset.serverTimeMs=String(serverNow)',context);
 vm.runInContext(source,context);
 vm.runInContext(`
 if(Date.now()!==serverNow||new Date().getTime()!==serverNow)throw Error('Page clock not aligned');
 if(parent.Date!==OriginalDate||Math.abs(parent.Date.now()-originalNow)>5000)throw Error('Parent clock changed');
 if(new Date(0).getTime()!==0||new Date('2020-01-01T00:00:00Z').getTime()!==1577836800000)throw Error('Explicit time changed');
 if(Date.parse('2020-01-01T00:00:00Z')!==OriginalDate.parse('2020-01-01T00:00:00Z')||Date.UTC(2020,0,1)!==OriginalDate.UTC(2020,0,1))throw Error('Static date contract changed');
 if(!(new Date() instanceof Date)||!(new Date() instanceof OriginalDate)||new Date().constructor!==Date)throw Error('Date identity changed');
 class ChildDate extends Date{};if(!(new ChildDate() instanceof ChildDate)||new ChildDate().getTime()!==serverNow)throw Error('Subclass changed');
 if(Date()!==new OriginalDate(serverNow).toString())throw Error('Callable Date changed');
 tick+=2000;if(Date.now()!==serverNow+2000)throw Error('Clock did not advance monotonically');
 `,context);
 const date=context.Date;vm.runInContext(source,context);assert.equal(context.Date,date,'Repeated adapter changed the clock');
}
for(const value of [undefined,'',0,'bad',NaN,Infinity,Date.now()+30000,Date.now()+86401000]){
 const context=vm.createContext({location:{pathname:'/grafana/d/fixture/'},document:{currentScript:{dataset:{serverTimeMs:value}}},performance:{now:()=>0}});
 vm.runInContext('window=globalThis;OriginalDate=Date',context);vm.runInContext(source,context);
 assert.equal(vm.runInContext('Date===OriginalDate',context),true,'Invalid/healthy clock was changed');
}
const context=vm.createContext({location:{pathname:'/'},document:{currentScript:{dataset:{serverTimeMs:Date.now()+2612000}}}});
vm.runInContext('window=globalThis;OriginalDate=Date',context);vm.runInContext(source,context);assert.equal(vm.runInContext('Date===OriginalDate',context),true);
console.log(JSON.stringify({passed:true,scope:'Grafana document only',stored_timestamps_changed:false}));
"""
        result = subprocess.run(
            ["node", "-e", harness, str(ROOT / "management/static/grafana-clock.js")],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["passed"])
