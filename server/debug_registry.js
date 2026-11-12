#!/usr/bin/env node
// Test 4khdhub through registry

const { getProvider } = require('./providers/registry.js');

async function test() {
    console.log('Testing 4khdhub through registry for Spider-Man: Brand New Day (TMDB 969681)');
    console.log('=' * 60);

    const prov = getProvider('4khdhub');
    console.log('Provider:', prov);

    if (!prov) {
        console.log('Provider not found!');
        return;
    }

    if (!prov.enabled) {
        console.log('Provider is disabled!');
        return;
    }

    try {
        console.log('Calling fetch...');
        const t0 = Date.now();
        const result = await prov.fetch({ tmdbId: '969681', type: 'movie', season: null, episode: null, imdbId: null, filters: {} });
        const duration = Date.now() - t0;
        console.log(`Duration: ${duration}ms`);
        console.log(`Result: ${result.length} streams`);
        result.forEach((s, i) => {
            console.log(`  ${i+1}. ${s.name} - ${s.quality}`);
            console.log(`     URL: ${s.url.substring(0, 80)}...`);
        });
    } catch (error) {
        console.error('Error:', error.message);
        console.error(error.stack);
    }
}

test();