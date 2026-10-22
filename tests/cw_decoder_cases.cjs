// Exercise the production decoder extracted from the rendered recordings page.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const page = fs.readFileSync(0, 'utf8');
const pieces = [
  page.slice(page.indexOf('    const MORSE_CHARACTERS'), page.indexOf('    function formatTime')),
  page.slice(page.indexOf('    function fftInPlace'), page.indexOf('    function robustScale')),
  page.slice(page.indexOf('    function percentile'), page.indexOf('    function setMorseCellStatus')),
];
const {decode, alphabet} = new Function('window', pieces.join('\n') +
  ';return {decode: decodeMorseAudioBuffer, alphabet: MORSE_CHARACTERS};')({setTimeout: f => f()});
const encoding = Object.fromEntries(Object.entries(alphabet).map(([pattern, letter]) => [letter, pattern]));

function audio(text, {wpm = 20, pitch = () => 700, rate = 8000, gain = () => 1,
                      noise = 0.03, continuous = false, channels = 1, birdie = null,
                      channelGains = null, highFrequencyNoise = false} = {}) {
  let seed = 42;
  const random = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 4294967296 - 0.5;
  };
  const runs = [{on: false, units: 10}];
  for (const [index, word] of text.split(' ').entries()) {
    if (index) runs.push({on: false, units: 4});
    for (const letter of word) {
      for (const element of encoding[letter]) {
        runs.push({on: true, units: element === '.' ? 1 : 3}, {on: false, units: 1});
      }
      runs.push({on: false, units: 2});
    }
  }
  runs.push({on: false, units: 10});
  const dot = 1.2 / wpm;
  const length = runs.reduce((sum, run) => sum + Math.round(run.units * dot * rate), 0);
  const samples = new Float32Array(length);
  let offset = 0;
  let phase = 0;
  let birdiePhase = 0;
  let interferencePhase = 0;
  for (const run of runs) {
    const count = Math.round(run.units * dot * rate);
    for (let i = 0; i < count; i += 1, offset += 1) {
      const time = offset / rate;
      // Integrating phase permits both drift and discontinuous frequency
      // corrections in the middle of dots/dashes, independently of keying.
      phase += 2 * Math.PI * pitch(time) / rate;
      if (birdie) birdiePhase += 2 * Math.PI * birdie(time) / rate;
      const envelope = continuous ? 1 : run.on
        ? Math.min(1, i / (rate * 0.002), (count - 1 - i) / (rate * 0.002)) : 0;
      const noiseLevel = typeof noise === 'function' ? noise(time) : noise;
      samples[offset] = 0.5 * gain(time) * envelope * Math.sin(phase) + noiseLevel * random();
      if (birdie) samples[offset] += 0.5 * Math.sin(birdiePhase);
      if (highFrequencyNoise) {
        // Wide, rapidly changing SDR interference above the CW passband.
        // At 8 kHz decimation these 6–14 kHz components alias into CW.
        interferencePhase += 2 * Math.PI * (10000 + 4000 * Math.sin(2 * Math.PI * 73 * time)) / rate;
        samples[offset] += 0.7 * Math.sin(interferencePhase);
      }
    }
  }
  const channelSamples = Array.from({length: channels}, (_, channel) =>
    channelGains ? samples.map(value => value * channelGains[channel]) : samples);
  return {duration: length / rate, length, sampleRate: rate,
    numberOfChannels: channels, getChannelData: channel => channelSamples[channel]};
}

async function main() {
  const message = 'CQ TEST DE AB1XYZ 73';
  const jumps = time => [400, 3000, 800, 2100][Math.floor(time / 0.71) % 4] - 80 * (time % 0.71);
  const cases = [
    ['steady 12 WPM', {wpm: 12}],
    ['steady 20 WPM', {wpm: 20}],
    ['weak tone in receiver noise', {gain: () => 0.2, noise: 0.3}],
    ['short dots at 35 WPM', {wpm: 35}],
    ['up/down retunes at 20 WPM', {wpm: 20, pitch: jumps}],
    ['up/down retunes at 35 WPM', {wpm: 35, pitch: jumps}],
    ['48 kHz receiver audio', {pitch: jumps, rate: 48000}],
    ['48 kHz SDR high-frequency interference', {rate: 48000, gain: () => .15, highFrequencyNoise: true}],
    ['44.1 kHz SDR high-frequency interference', {rate: 44100, gain: () => .15, highFrequencyNoise: true}],
    ['96 kHz SDR high-frequency interference', {rate: 96000, gain: () => .15, highFrequencyNoise: true}],
    ['SDR interference during fast retunes', {rate: 48000, wpm: 35, pitch: jumps,
      highFrequencyNoise: true}],
    ['SDR interference with opposite-polarity stereo', {rate: 48000, channels: 2,
      channelGains: [1, -1], highFrequencyNoise: true}],
    ['upper CW band with SDR interference', {rate: 44100, pitch: () => 3400,
      highFrequencyNoise: true}],
    ['44.1 kHz stereo audio', {pitch: jumps, rate: 44100, channels: 2}],
    ['opposite-polarity stereo audio', {channels: 2, channelGains: [1, -1]}],
    ['weak audio on right channel only', {channels: 2, channelGains: [0, 1], gain: () => 0.2, noise: 0.3}],
    ['high audio pitch', {pitch: () => 3100}],
    ['fading and drift', {pitch: t => 1300 - 22 * t,
      gain: t => 0.5 + 0.4 * Math.sin(0.7 * t), noise: 0.015}],
    ['strong retuning birdie', {pitch: t => 600 + 150 * (Math.floor(t / 2) % 2),
      gain: () => 0.2, noise: 0.01, birdie: t => 2200 + 150 * (Math.floor(t / 2) % 2)}],
    ['nearby retuning birdie', {pitch: t => 600 + 150 * (Math.floor(t / 2) % 2),
      gain: () => 0.2, noise: 0.01, birdie: t => 1100 + 150 * (Math.floor(t / 2) % 2)}],
    ['changing signal and noise', {pitch: t => 1000 - 20 * t,
      gain: t => 0.4 + 0.2 * Math.sin(0.4 * t), noise: t => 0.08 + 0.04 * Math.cos(0.6 * t)}],
  ];
  for (const [name, options] of cases) {
    const progress = [];
    const result = await decode(audio(message, options), value => progress.push(value));
    assert.equal(result.text, message, name);
    assert.ok(Math.abs(result.wordsPerMinute - (options.wpm || 20)) < 2, `${name}: speed estimate`);
    assert.equal(progress.at(-1), 100, `${name}: completed progress`);
    assert.ok(progress.every((value, index) => index === 0 || value >= progress[index - 1]));
    console.log(`PASS ${name}`);
  }
  for (const [name, options] of [
    ['silence', {gain: () => 0, noise: 0}],
    ['noise only', {gain: () => 0, noise: 0.2}],
    ['SDR high-frequency interference only', {rate: 48000, gain: () => 0,
      highFrequencyNoise: true}],
    ['unkeyed carrier', {continuous: true}],
    ['retuning birdie only', {gain: () => 0, birdie: t => 2200 + 150 * (Math.floor(t / 2) % 2)}],
  ]) {
    await assert.rejects(decode(audio(message, options)), /Morse|keyed|pulses/i, name);
    console.log(`PASS ${name}`);
  }
  await assert.rejects(decode({duration: 0}), /no decodable audio/);
  console.log('PASS empty recording');

  // Optional local capture: recordings are not checked into the repository.
  if (process.argv[2]) {
    const wav = fs.readFileSync(process.argv[2]);
    assert.equal(wav.toString('ascii', 0, 4), 'RIFF');
    assert.equal(wav.toString('ascii', 8, 12), 'WAVE');
    let format, data;
    for (let offset = 12; offset + 8 <= wav.length;) {
      const size = wav.readUInt32LE(offset + 4);
      const id = wav.toString('ascii', offset, offset + 4);
      if (id === 'fmt ') format = wav.subarray(offset + 8, offset + 8 + size);
      if (id === 'data') data = wav.subarray(offset + 8, offset + 8 + size);
      offset += 8 + size + size % 2;
    }
    assert.ok(format && data, 'WAV format and sample chunks');
    assert.equal(format.readUInt16LE(0), 1, 'PCM recording');
    assert.equal(format.readUInt16LE(14), 16, '16-bit samples');
    const channels = format.readUInt16LE(2);
    const rate = format.readUInt32LE(4);
    const length = data.length / (2 * channels);
    const samples = Array.from({length: channels}, (_, channel) =>
      Float32Array.from({length}, (_, index) =>
        data.readInt16LE((index * channels + channel) * 2) / 32768));
    const progress = [];
    const result = await decode({duration: length / rate, length, sampleRate: rate,
      numberOfChannels: channels, getChannelData: channel => samples[channel]},
      value => progress.push(value));
    assert.ok(result.pulseCount > 200, 'recover the beacon keying');
    assert.match(result.text, /OK0LSR = 73 FROM LASARSAT AR/);
    assert.ok(Math.abs(result.wordsPerMinute - 20) < 2);
    assert.equal(progress.at(-1), 100);
    assert.ok(progress.every((value, index) => index === 0 || value >= progress[index - 1]));
    console.log(`PASS LASARSAT capture: ${result.pulseCount} pulses, ${result.wordsPerMinute.toFixed(1)} WPM`);
  }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
