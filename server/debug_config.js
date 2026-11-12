#!/usr/bin/env node
// Test config loading

const { config } = require('./utils/config.js');

console.log('Config loaded:');
console.log('  tmdbApiKeys:', config.tmdbApiKeys);
console.log('  tmdbApiKey:', config.tmdbApiKey);
console.log('  enable4khdhubProvider:', config.enable4khdhubProvider);