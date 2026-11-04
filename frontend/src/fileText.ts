// What a CSV, OFX or text file someone picked says, whichever way it was saved: UTF-8, with or
// without its byte order mark, else Windows-1252, the encoding Excel on Windows writes for "CSV".
// Read as UTF-8 alone (File.text()), a Windows-1252 file turns every accented letter into "�"
// (José becomes Jos�) without a word, and a tenant's name comes into the books wrong.
export async function fileText(file: Blob): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return windows1252(bytes);
  }
}

// Bytes 0x80 to 0x9F of Windows-1252, as the Encoding Standard maps them (its five unused bytes
// as the control characters of the same number); every other byte is the Latin-1 letter of its
// number. Decoded here, not by TextDecoder, which some runtimes read as Latin-1 alone.
const HIGH = [
  0x20ac, 0x81, 0x201a, 0x192, 0x201e, 0x2026, 0x2020, 0x2021, 0x2c6, 0x2030, 0x160, 0x2039, 0x152, 0x8d,
  0x17d, 0x8f, 0x90, 0x2018, 0x2019, 0x201c, 0x201d, 0x2022, 0x2013, 0x2014, 0x2dc, 0x2122, 0x161, 0x203a,
  0x153, 0x9d, 0x17e, 0x178,
];

function windows1252(bytes: Uint8Array): string {
  let text = "";
  for (const byte of bytes) text += String.fromCharCode(byte >= 0x80 && byte <= 0x9f ? HIGH[byte - 0x80]! : byte);
  return text;
}
