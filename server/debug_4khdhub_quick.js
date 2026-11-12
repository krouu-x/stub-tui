#!/usr/bin/env node
// Quick test - just search and count

const { get4KHDHubStreams } = require('./providers/4khdhub.js');

async function test() {
    console.log('Testing 4khdhub provider for Spider-Man: Brand New Day (TMDB 969681)');
    console.log('=' * 60);

    try {
        const streams = await get4KHDHubStreams('969681', 'movie');
        console.log(`\nResult: ${streams.length} streams`);
        streams.slice(0, 10).forEach((s, i) => {
            console.log(`  ${i+1}. ${s.name} - ${s.quality} - ${s.url.substring(0, 80)}...`);
        });
        if (streams.length > 10) {
            console.log(`  ... and ${streams.length - 10} more`);
        }
    } catch (error) {
        console.error('Error:', error.message);
        console.error(error.stack);
    }
}

test();