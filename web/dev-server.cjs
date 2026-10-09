const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const root=__dirname, upstream=new URL(process.env.API_ORIGIN||'http://127.0.0.1:8000');
if(upstream.protocol!=='http:')throw Error('API_ORIGIN must use http');
http.createServer((req,res)=>{
 const pathname=new URL(req.url,'http://localhost').pathname;
 if(pathname.startsWith('/api/')||pathname.startsWith('/audio/')){
  const target=http.request({hostname:upstream.hostname,port:upstream.port||80,path:req.url,method:req.method,headers:{...req.headers,host:upstream.host}},r=>{res.writeHead(r.statusCode,r.headers);r.pipe(res);});
  target.on('error',()=>{if(!res.headersSent)res.writeHead(502,{'Content-Type':'application/json'});res.end(JSON.stringify({error:'Backend unavailable on '+upstream.origin}));});req.pipe(target);return;
 }
 const name=pathname==='/'?'index.html':pathname.slice(1);
 if(!/^[\w.-]+\.(html|css|js)$/.test(name)){res.writeHead(404);res.end();return;}
 fs.readFile(path.join(root,name),(err,data)=>{if(err){res.writeHead(404);res.end();return;}res.setHeader('Content-Type',{'html':'text/html','js':'text/javascript','css':'text/css'}[name.split('.').pop()]+'; charset=utf-8');res.end(data);});
}).listen(Number(process.env.PORT||5173),'127.0.0.1',()=>console.log('Differential UI: http://127.0.0.1:'+(process.env.PORT||5173)));
