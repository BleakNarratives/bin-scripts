#!/usr/bin/env node

import { createRequire } from 'module';
import { readFileSync, existsSync } from 'fs';
import { join } from 'path';
import { argv, env, exit } from 'process';

const require = createRequire(import.meta.url);

const tokenPath = join(env.HOME, '.puter_token');
if (!existsSync(tokenPath)) {
    console.error('[puter_bridge] No token found at ~/.puter_token');
    exit(1);
}
const token = readFileSync(tokenPath, 'utf8').trim();

const { puter } = require(join(env.HOME, 'bin/node_modules/@heyputer/puter.js/src/index.js'));
puter.setAuthToken(token);

const prompt = argv.slice(2).join(' ');
if (!prompt) {
    console.error('[puter_bridge] Usage: puter "your prompt"');
    exit(1);
}

(async () => {
    try {
        const response = await puter.ai.chat(prompt);
        const text = response?.message?.content
            ?? response?.text
            ?? JSON.stringify(response);
        console.log(text);
    } catch (err) {
        console.error('[puter_bridge] Error:', err.message || err);
        exit(1);
    }
})();
