// QMC2 RC4 audio stream transform; song keys are accepted through stdin only.
const fs = require('node:fs');
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', () => {
  try {
    const options = JSON.parse(input);
    const key = Buffer.from(options.key, 'base64');
    const n = key.length;
    if (n < 8 || n > 2048) throw new Error('Unsupported audio key length');
    const seed = new Uint8Array(n);
    for (let i = 0; i < n; ++i) seed[i] = i & 255;
    let j = 0;
    for (let i = 0; i < n; ++i) {
      j = (j + seed[i] + key[i]) % n;
      [seed[i], seed[j]] = [seed[j], seed[i]];
    }
    let hash = 1;
    for (const value of key) {
      if (!value) continue;
      const next = Math.imul(hash, value) >>> 0;
      if (!next || next <= hash) break;
      hash = next;
    }
    function skip(id) {
      const value = key[id % n];
      return value ? Math.trunc(hash / ((id + 1) * value) * 100) % n : 0;
    }
    const source = fs.openSync(options.source, 'r');
    const target = fs.openSync(options.output, 'wx');
    const block = Buffer.allocUnsafe(5120 * 128);
    let offset = 0, bytes;
    try {
      while ((bytes = fs.readSync(source, block)) > 0) {
        if (n <= 300) {
          for (let i = 0; i < bytes; ++i) {
            let position = offset + i;
            if (position > 0x7fff) position %= 0x7fff;
            const index = (position * position + 71214) % n;
            const shift = (index + 4) & 7;
            block[i] ^= ((key[index] << shift) | (key[index] >> shift)) & 255;
          }
          fs.writeSync(target, block, 0, bytes);
          offset += bytes;
          continue;
        }
        let cursor = 0;
        if (offset === 0) {
          for (; cursor < Math.min(128, bytes); ++cursor) block[cursor] ^= key[skip(cursor)];
        }
        while (cursor < bytes) {
          const position = offset + cursor;
          const length = Math.min(5120 - position % 5120, bytes - cursor);
          const box = seed.slice();
          const discard = position % 5120 + skip(Math.floor(position / 5120));
          let a = 0, b = 0;
          for (let i = -discard; i < length; ++i) {
            a = (a + 1) % n;
            b = (b + box[a]) % n;
            const tmp = box[a]; box[a] = box[b]; box[b] = tmp;
            if (i >= 0) block[cursor + i] ^= box[(box[a] + box[b]) % n];
          }
          cursor += length;
        }
        fs.writeSync(target, block, 0, bytes);
        offset += bytes;
      }
    } finally { fs.closeSync(source); fs.closeSync(target); }
    process.stdout.write(JSON.stringify({ bytes: offset }));
  } catch (error) { process.stderr.write(error.message); process.exitCode = 1; }
});
