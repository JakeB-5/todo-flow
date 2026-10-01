const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../src/todo_flow/web');

test('verification meaning and diagnostic intent survive language changes',()=>{
  const context=vm.createContext({
    document:{documentElement:{},querySelectorAll:()=>[],getElementById:()=>({value:''})},
    localStorage:{getItem:()=>null,setItem(){}},
    refreshLabels(){}
  });
  vm.runInContext(fs.readFileSync(path.join(root,'i18n.js'),'utf8'),context);
  const source=fs.readFileSync(path.join(root,'app.js'),'utf8');
  vm.runInContext(source.slice(source.indexOf('function verificationLabel('),source.indexOf('function renderActivity(')),context);
  vm.runInContext(source.slice(source.indexOf('function taskDescription('),source.indexOf('function activityPages(')),context);
  const evaluate=code=>vm.runInContext(code,context);
  for(const [language,passed,failed,inconclusive] of [
    ['en','Passed','Failed','Inconclusive'],['ko','통과','실패','판정 불가']
  ]) {
    evaluate(`applyLanguage('${language}')`);
    assert.equal(evaluate('verificationLabel({verificationOk:1})'),passed);
    assert.equal(evaluate('verificationLabel({verificationOk:0})'),inconclusive);
    assert.equal(evaluate('verificationLabel({verificationOk:0,verificationOutcome:"failed"})'),failed);
    assert.equal(evaluate('verificationLabel({verificationOk:1,verificationOutcome:"inconclusive"})'),inconclusive);
    assert.equal(evaluate('verificationLabel({verificationOk:1,verificationOutcome:"passed",verificationComplete:0})'),inconclusive);
    assert.equal(evaluate('verificationLabel({verificationOk:1,verificationCancelled:1})'),inconclusive);
    const description=evaluate('taskDescription({kind:"assess",intent:"verification-diagnosis"})');
    assert.equal(description,language==='en'
      ?'Diagnose the check and reverify the same candidate after recovery.'
      :'검사 원인을 진단하고 복구 후 같은 후보를 재검증합니다.');
  }
});
