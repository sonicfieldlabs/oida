import test from 'node:test';
import assert from 'node:assert/strict';
import {register, localOrigin} from '../integrations/pi/index.mjs';
const response = (value, status=200) => new Response(JSON.stringify(value), {status});
test('Pi tools bind fixed owner routes and stable identity without retaining memory', async () => {
 const tools = {}, calls=[];register({registerTool(t){tools[t.name]=t;}},'http://127.0.0.1:8766',async (url,opts)=>{calls.push([url,opts]);return response({status:'complete'});});
 assert.deepEqual(Object.keys(tools),['oida_capabilities','oida_listen','oida_operation']);
 const params={path:'/fixture.wav',operation_id:'one',permission:true};
 await tools.oida_listen.execute('host1',params);await tools.oida_listen.execute('host2',params);
 const body=JSON.parse(calls[0][1].body);assert.equal(body.operation_id,JSON.parse(calls[1][1].body).operation_id);
 assert.equal(body.remember,false);assert.equal(body.ephemeral_delivery,true);assert.equal(body.privacy_mode,'incognito');
 await assert.rejects(tools.oida_listen.execute('x',{...params,permission:false}));
 await tools.oida_operation.execute('x',{operation_id:'one',cancel:true});assert.match(calls.at(-1)[0],/\/operations\/pi_[a-f0-9]{64}\/cancel$/);
});
test('Pi cancellation and failure fence owner commit without replay', async()=>{
 let calls=[];register({registerTool(t){if(t.name==='oida_listen')calls.tool=t;}},'http://localhost:8766',async(url)=>{calls.push(url);if(url.endsWith('/cancel'))return response({status:'cancelled'});throw new Error('connection lost');});
 await assert.rejects(calls.tool.execute('x',{path:'/fixture.wav',operation_id:'one',permission:true}),/unconfirmed/);
 assert.equal(calls.length,2);assert.match(calls[1],/cancel$/);
 const c=new AbortController();c.abort();await assert.rejects(calls.tool.execute('x',{path:'/fixture.wav',operation_id:'two',permission:true},c.signal),/before dispatch/);assert.equal(calls.length,2);
});
test('Pi refuses remote origins, redirects and oversized responses',async()=>{
 for(const url of ['https://127.0.0.1:8766','http://remote.example:8766','http://user@localhost:8766','http://localhost:8766/private'])assert.throws(()=>localOrigin(url));
 const tools={};register({registerTool(t){tools[t.name]=t;}},'http://localhost:8766',async(_,opts)=>{assert.equal(opts.redirect,'error');return new Response('x'.repeat(2*1024*1024+1));});
 await assert.rejects(tools.oida_capabilities.execute('x',{}),/Oversized/);
});
test('Owner duplicate refusal returns a receipt without replay or cancellation',async()=>{
 const tools={},calls=[];register({registerTool(t){tools[t.name]=t;}},'http://localhost:8766',async(url)=>{calls.push(url);return response({detail:{receipt:{id:'existing',status:'complete'}}},409);});
 const result=await tools.oida_listen.execute('x',{path:'/fixture.wav',operation_id:'same',permission:true});
 assert.equal(result.details.status,'existing_operation');assert.equal(result.details.report_available,false);assert.equal(calls.length,1);
});
