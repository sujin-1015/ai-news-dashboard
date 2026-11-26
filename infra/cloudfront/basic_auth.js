// CloudFront Function (cloudfront-js-2.0, viewer-request): 사이트 전체에 아이디/비밀번호(Basic 인증)를 건다.
//
// - 비밀번호는 코드·저장소·CloudFormation에 없다. CloudFront KeyValueStore의 키 "auth_sha256"에
//   sha256("아이디:비밀번호") 16진수 값만 저장한다 (scripts/set-site-password.ps1).
// - 값이 아직 없으면 모두 거부한다 (fail closed).
// - 캐시보다 먼저 실행되므로 캐시된 파일도 보호된다. 비용: 100만 회당 $0.10 (월 200만 회 무료).
import cf from 'cloudfront';
import crypto from 'crypto';

const kvs = cf.kvs('__KVS_ID__');

function deny() {
  return {
    statusCode: 401,
    statusDescription: 'Unauthorized',
    headers: { 'www-authenticate': { value: 'Basic realm="AI News Dashboard", charset="UTF-8"' } },
  };
}

async function handler(event) {
  const request = event.request;
  const header = request.headers.authorization;
  if (!header || header.value.indexOf('Basic ') !== 0) return deny();

  let expected;
  try {
    expected = await kvs.get('auth_sha256');
  } catch (e) {
    return deny(); // 비밀번호 미설정 → 거부
  }

  let decoded;
  try {
    decoded = Buffer.from(header.value.slice(6), 'base64').toString('utf8');
  } catch (e) {
    return deny();
  }
  const actual = crypto.createHash('sha256').update(decoded).digest('hex');
  return actual === expected ? request : deny();
}
