import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../server/connect.js',import.meta.url),'utf8');
const functionSource=source.match(/ function makeIncyImport\(bytes\)\{[^\n]+\}/)?.[0];
assert.ok(functionSource);
const encode=vm.runInNewContext('('+functionSource.trim()+')',{btoa});
test('INCY direct import preserves every configuration byte and includes no web credentials',()=>{
 const payload=Buffer.from('[Interface]\nPrivateKey = fixture-only\n# Пример\n[Peer]\nPublicKey = fixture\n','utf8');
 const uri=encode(payload);assert.ok(uri.startsWith('incy://import/'));
 assert.deepEqual(Buffer.from(uri.slice('incy://import/'.length),'base64'),payload);
 assert.equal(uri.includes('https://connect.'),false);
 assert.equal(uri.includes('access_code'),false);
});
