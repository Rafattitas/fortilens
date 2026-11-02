"""Exercise actual inline/browser transport functions with synthetic Node mocks."""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
HARNESS = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
let input='';process.stdin.setEncoding('utf8');process.stdin.on('data',s=>input+=s);
process.stdin.on('end',async()=>{
 const config=JSON.parse(input),secret='synthetic-browser-private-detail';
 let calls=0,jsonReads=0,signouts=0,lastOptions,notice='';
 const timers=new Set(),nodes=new Map();
 const node=id=>{if(!nodes.has(id))nodes.set(id,{disabled:false,textContent:'Refresh',hidden:false,replaceChildren(){},value:''});return nodes.get(id)};
 const context={AbortController,Promise,URLSearchParams,
  state:{csrf:'synthetic-csrf',auth:'synthetic-basic',portal:true,busy:false,pending:false,report:null},
  setTimeout(fn,ms){let id;id=setTimeout(()=>{timers.delete(id);fn()},Math.min(ms,12));timers.add(id);return id},
  clearTimeout(id){timers.delete(id);clearTimeout(id)},
  signedOut(){signouts++;context.state.csrf='';context.state.auth=''},
  $:node,notice(text){notice=text},message(text){notice=text},
  params(){return new URLSearchParams({period:'today'})},
  fetch:async(path,options)=>{
   calls++;lastOptions=options;
   if(config.scenario==='network')throw Error(secret);
   if(config.scenario==='fetch_timeout')return new Promise(()=>{});
   return {status:config.status||200,ok:!config.status||config.status<400,json:async()=>{
    jsonReads++;
    if(config.scenario==='body_timeout')return new Promise(()=>{});
    if(config.scenario==='bad_json'||config.scenario==='unauthorized')throw Error(secret);
    return config.payload;
   }};
  }
 };
 vm.createContext(context);vm.runInContext(config.source,context);
 try{
  if(config.scenario==='button'){
   const button={id:'synthetic',disabled:false,textContent:'Apply'};
   await context.action(button,async()=>{throw Error(secret)});
   assert.equal(button.disabled,false);assert.equal(button.textContent,'Apply');assert(!notice.includes(secret));
  }else if(config.scenario==='report_cleanup'){
   context.state.pending=true;context.state.report={private:secret};
   context.api=async()=>{calls++;throw Error(secret)};
   await context.loadReport();
   assert.equal(context.state.busy,false);assert.equal(context.state.pending,false);
   assert.equal(context.state.report,null);assert.equal(node('refresh').disabled,false);
   assert.equal(calls,1);assert(!notice.includes(secret));
  }else{
   let result,error;
   try{result=await context.api(config.path||'/api/synthetic',...(config.write?[{revision:0}]:[]))}catch(e){error=e}
   assert.equal(calls,1);
   if(config.expect_error){
    assert(error);assert.equal(error.code,config.expect_error);assert(!context.errorText(error).includes(secret));
    if(config.write&&['network','timeout','invalid_response'].includes(error.code))assert(error.message.includes('may have completed'));
   }else{assert.equal(error,undefined);assert.deepEqual(JSON.parse(JSON.stringify(result)),config.payload)}
   if(config.scenario==='unauthorized'){assert.equal(signouts,1);assert.equal(jsonReads,0)}
   if(config.scenario==='success'&&config.kind==='management'&&config.write){
    assert.equal(lastOptions.headers['X-CSRF-Token'],'synthetic-csrf');assert.equal(lastOptions.method,'POST');
   }
  }
  assert.equal(timers.size,0);process.stdout.write('PASS');
 }catch(error){process.stderr.write(error.stack);process.exitCode=1}
});
"""


@unittest.skipUnless(NODE, "Browser transport tests require development-only Node.js.")
class FrontendErrorTests(unittest.TestCase):
    def source(self, kind, extra=None):
        path = "management/static/app.js" if kind == "management" else "portable/reporting.html"
        text = (ROOT / path).read_text(encoding="utf-8")
        start = text.index("// Transport boundary:")
        end = text.index("// End transport boundary.", start)
        source = text[start:end]
        if extra:
            match = re.search(r"(?m)^async function " + extra + r"\([^\n]*", text)
            self.assertIsNotNone(match)
            source += "\n" + match[0]
        return source

    def run_case(self, kind, scenario, **options):
        extra = (
            "action"
            if scenario == "button"
            else "loadReport"
            if scenario == "report_cleanup"
            else None
        )
        payload = {
            "kind": kind,
            "scenario": scenario,
            "source": self.source(kind, extra),
            **options,
        }
        result = subprocess.run(
            [NODE, "-e", HARNESS],
            input=json.dumps(payload),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "PASS")

    def test_success_keeps_payload_and_management_write_headers(self):
        for kind in ("management", "reporting"):
            with self.subTest(kind=kind):
                self.run_case(
                    kind, "success", payload={"status": "synthetic"}, write=kind == "management"
                )

    def test_network_failure_is_private_and_never_retried(self):
        for kind in ("management", "reporting"):
            with self.subTest(kind=kind):
                self.run_case(kind, "network", expect_error="network", write=kind == "management")

    def test_deadline_covers_fetch_and_body_and_clears_timer(self):
        for kind in ("management", "reporting"):
            for scenario in ("fetch_timeout", "body_timeout"):
                with self.subTest(kind=kind, scenario=scenario):
                    self.run_case(
                        kind, scenario, expect_error="timeout", write=kind == "management"
                    )

    def test_bad_json_and_non_object_envelopes_are_private(self):
        for kind in ("management", "reporting"):
            for scenario, value in (("bad_json", {}), ("bad_shape", None), ("bad_shape", [])):
                with self.subTest(kind=kind, scenario=scenario, value=value):
                    self.run_case(kind, scenario, payload=value, expect_error="invalid_response")

    def test_non_json_401_revokes_local_session_before_body_read(self):
        for kind in ("management", "reporting"):
            with self.subTest(kind=kind):
                self.run_case(
                    kind, "unauthorized", status=401, expect_error="authentication_required"
                )

    def test_known_error_contract_is_preserved_but_raw_message_is_not_trusted(self):
        for kind in ("management", "reporting"):
            with self.subTest(kind=kind):
                self.run_case(
                    kind,
                    "known",
                    status=503,
                    payload={"error": "helper_unavailable", "message": "Service unavailable."},
                    expect_error="helper_unavailable",
                )
                self.run_case(
                    kind,
                    "unknown",
                    status=503,
                    payload={"message": "synthetic-browser-private-detail"},
                    expect_error="operation_unavailable",
                )

    def test_required_success_fields_cannot_create_a_false_session_or_report(self):
        for kind, path in (
            ("management", "/api/session"),
            ("reporting", "/api/report?period=today"),
        ):
            with self.subTest(kind=kind):
                self.run_case(
                    kind, "bad_shape", path=path, payload={}, expect_error="invalid_response"
                )

    def test_action_and_report_cleanup_do_not_leak_or_retry_failures(self):
        self.run_case("management", "button")
        self.run_case("reporting", "report_cleanup")


if __name__ == "__main__":
    unittest.main()
