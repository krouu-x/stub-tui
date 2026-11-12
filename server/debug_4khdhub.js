#!/usr/bin/env node
// Test 4khdhub provider directly with error details

const { get4KHDHubStreams } = require('./providers/4khdhub.js');
const { getTmdbApiKey } = require('./utils/tmdbKey.js');

async function test() {
    console.log('TMDB API Key:', getTmdbApiKey());
    console.log('Testing 4khdhub provider for Spider-Man: Brand New Day (TMDB 969681)');
    console.log('=' * 60);

    try {
        const streams = await get4KHDHubStreams('969681', 'movie');
        console.log(`\nResult: ${streams.length} streams`);
        streams.forEach((s, i) => {
            console.log(`  ${i+1}. ${s.name} - ${s.quality}`);
            console.log(`     URL: ${s.url}`);
        });
    } catch (error) {
        console.error('Error:', error.message);
        console.error(error.stack);
    }
}

test();