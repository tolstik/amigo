<?php
declare(strict_types=1);

/* Return the current v2 OAuth pair to the preserved legacy MariaDB on rollback. */

function fail_handback(string $message): never
{
    fwrite(STDERR, "legacy-token-handback.php: {$message}\n");
    exit(1);
}

function legacy_literal(array $token): string
{
    if ($token[0] !== T_CONSTANT_ENCAPSED_STRING) {
        fail_handback('legacy database configuration is malformed');
    }
    $quote = $token[1][0] ?? '';
    $body = substr($token[1], 1, -1);
    if ($quote === "'") {
        return str_replace(["\\\\", "\\'"], ["\\", "'"], $body);
    }
    if ($quote === '"') {
        return stripcslashes($body);
    }
    fail_handback('unsupported legacy string literal');
}

function legacy_database_arguments(string $path): array
{
    $source = @file_get_contents($path);
    if ($source === false) {
        fail_handback('legacy Withings source is unreadable');
    }
    $tokens = token_get_all($source);
    $count = count($tokens);
    for ($index = 0; $index < $count; $index++) {
        $token = $tokens[$index];
        if (!is_array($token) || $token[0] !== T_STRING || strcasecmp($token[1], 'mysqli_connect') !== 0) {
            continue;
        }
        $arguments = [];
        $depth = 0;
        for ($cursor = $index + 1; $cursor < $count; $cursor++) {
            $candidate = $tokens[$cursor];
            if ($candidate === '(') {
                $depth++;
                continue;
            }
            if ($candidate === ')') {
                $depth--;
                if ($depth === 0) {
                    break;
                }
                continue;
            }
            if ($depth === 1 && is_array($candidate) && $candidate[0] === T_CONSTANT_ENCAPSED_STRING) {
                $arguments[] = legacy_literal($candidate);
            }
        }
        if (count($arguments) >= 3) {
            return array_slice($arguments, 0, 3);
        }
    }
    fail_handback('legacy database configuration is missing');
}

function token_file(string $path): string
{
    if (!preg_match('~^/run/amigo-token-handoff\.[A-Za-z0-9]+/(access_token|refresh_token)$~', $path)) {
        fail_handback('unexpected token handoff path');
    }
    if (is_link($path) || !is_file($path)) {
        fail_handback('token handoff file is missing or unsafe');
    }
    $mode = fileperms($path);
    if ($mode === false || ($mode & 0077) !== 0 || fileowner($path) !== 0) {
        fail_handback('token handoff file permissions are unsafe');
    }
    $value = @file_get_contents($path);
    if ($value === false || $value === '' || str_contains($value, "\n") || str_contains($value, "\r") || str_contains($value, "\0")) {
        fail_handback('token handoff value failed validation');
    }
    return $value;
}

if (PHP_SAPI !== 'cli' || !function_exists('posix_geteuid') || posix_geteuid() !== 0 || $argc !== 3) {
    fail_handback('run as root with explicit access and refresh token files');
}

$accessToken = token_file($argv[1]);
$refreshToken = token_file($argv[2]);
[$dbHost, $dbUser, $dbPassword] = legacy_database_arguments('/srv/cron/get_withings.php');

mysqli_report(MYSQLI_REPORT_OFF);
$database = @new mysqli($dbHost, $dbUser, $dbPassword, 'amigo');
if ($database->connect_errno !== 0) {
    fail_handback('cannot connect to the preserved legacy integration database');
}
$database->begin_transaction();
$countResult = @$database->query('SELECT COUNT(*) AS token_rows FROM amigo.seting');
$countRow = $countResult === false ? null : $countResult->fetch_assoc();
if (!is_array($countRow) || (int) ($countRow['token_rows'] ?? 0) !== 1) {
    $database->rollback();
    fail_handback('legacy token table must contain exactly one row');
}
$statement = @$database->prepare(
    'UPDATE amigo.seting SET access_token = ?, refresh_token = ?, last_update = NOW()'
);
if ($statement === false) {
    $database->rollback();
    fail_handback('cannot prepare legacy token update');
}
$statement->bind_param('ss', $accessToken, $refreshToken);
if (!$statement->execute()) {
    $database->rollback();
    fail_handback('cannot update the legacy token row');
}
$statement->close();
$database->commit();
$database->close();

fwrite(STDERR, "Current Withings OAuth credentials were returned to the legacy collector.\n");
