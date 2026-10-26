import { totpCode, parseTotp, base32Decode } from '../totp.mjs';
import assert from 'node:assert/strict';
// RFC 6238 appendix B vectors (8 digits). Seeds as base32.
const b32 = (buf) => { const A='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'; let bits=0,v=0,o=''; for (const b of buf){v=(v<<8)|b;bits+=8;while(bits>=5){o+=A[(v>>>(bits-5))&31];bits-=5}} if(bits>0)o+=A[(v<<(5-bits))&31]; return o; };
const s1 = b32(Buffer.from('12345678901234567890'));
const s256 = b32(Buffer.from('12345678901234567890123456789012'));
const s512 = b32(Buffer.from('1234567890123456789012345678901234567890123456789012345678901234'));
for (const [t, sha1, sha256, sha512] of [[59,'94287082','46119246','90693936'],[1111111109,'07081804','68084774','25091201'],[20000000000,'65353130','77737706','47863826']]) {
  assert.equal(totpCode({secret:s1,digits:8,algorithm:'SHA1'}, t*1000).code, sha1);
  assert.equal(totpCode({secret:s256,digits:8,algorithm:'SHA256'}, t*1000).code, sha256);
  assert.equal(totpCode({secret:s512,digits:8,algorithm:'SHA512'}, t*1000).code, sha512);
}
assert.deepEqual(base32Decode('mfrg gzdf mztq'), Buffer.from('abcdefg'));
const spec = parseTotp('otpauth://totp/GitHub:alice%40x.com?secret=JBSWY3DPEHPK3PXP&issuer=GitHub&digits=6&period=30');
assert.equal(spec.issuer, 'GitHub'); assert.equal(spec.account, 'alice@x.com'); assert.equal(spec.secret, 'JBSWY3DPEHPK3PXP');
assert.throws(() => parseTotp('not base32 !!'));
assert.throws(() => parseTotp('otpauth://hotp/x?secret=JBSWY3DPEHPK3PXP'));
assert.equal(totpCode({secret:'JBSWY3DPEHPK3PXP'}, 59000).secondsLeft, 1);
console.log('totp tests pass');
