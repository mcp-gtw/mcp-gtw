import { readFileSync } from 'node:fs';
const { McpGtwProvider } = await import(new URL('../../../mcp-gtw-provider/src/index.js', import.meta.url));
const config = JSON.parse(readFileSync(process.env.TEST_CONFIG,'utf8'));
const providers = config.channels.map(channel=> {
    const provider=new McpGtwProvider({url:'ws://127.0.0.1:19480/provider?token='+config[channel].provider,reconnect:false});
    provider.registerTool({name:'identity',inputSchema:{type:'object',properties:{}}},()=>({channel}));
    provider.connect();
    return provider;
});
process.on('SIGTERM',()=>{for(const p of providers)p.disconnect();process.exit(0);});
