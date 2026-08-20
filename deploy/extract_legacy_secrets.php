<?php
declare(strict_types=1);

/*
 * One-time production migration helper. It reads the preserved legacy PHP
 * configuration and MariaDB token row, then writes only root-owned secret
 * files. Secret values are never written to stdout or command arguments.
 */

function fail_closed(string $message): never
{
    fwrite(STDERR, "extract_legacy_secrets.php: {$message}\n");
    exit(1);
}

function tokens(string $path): array
{
    $source = @file_get_contents($path);
    if ($source === false) {
        fail_closed("required legacy source is unreadable");
    }
    return token_get_all($source);
}

function literal_value(array $token): string
{
    if ($token[0] !== T_CONSTANT_ENCAPSED_STRING) {
        fail_closed("expected a constant string in legacy source");
    }
    $quote = $token[1][0] ?? '';
    $body = substr($token[1], 1, -1);
    if ($quote === "'") {
        return str_replace(["\\\\", "\\'"], ["\\", "'"], $body);
    }
    if ($quote === '"') {
        return stripcslashes($body);
    }
    fail_closed("unsupported legacy string literal");
}

function array_string_value(array $tokens, string $wantedKey): string
{
    $count = count($tokens);
    for ($index = 0; $index < $count; $index++) {
        $token = $tokens[$index];
        if (!is_array($token) || $token[0] !== T_CONSTANT_ENCAPSED_STRING) {
            continue;
        }
        if (literal_value($token) !== $wantedKey) {
            continue;
        }
        for ($cursor = $index + 1; $cursor < min($count, $index + 12); $cursor++) {
            if (is_array($tokens[$cursor]) && $tokens[$cursor][0] === T_DOUBLE_ARROW) {
                for ($valueIndex = $cursor + 1; $valueIndex < min($count, $cursor + 8); $valueIndex++) {
                    $value = $tokens[$valueIndex];
                    if (is_array($value) && $value[0] === T_CONSTANT_ENCAPSED_STRING) {
                        return literal_value($value);
                    }
                }
            }
        }
    }
    fail_closed("required legacy integration setting is missing");
}

function variable_string_value(array $tokens, string $variable): string
{
    $count = count($tokens);
    for ($index = 0; $index < $count; $index++) {
        $token = $tokens[$index];
        if (!is_array($token) || $token[0] !== T_VARIABLE || $token[1] !== $variable) {
            continue;
        }
        for ($cursor = $index + 1; $cursor < min($count, $index + 12); $cursor++) {
            $candidate = $tokens[$cursor];
            if (is_array($candidate) && $candidate[0] === T_CONSTANT_ENCAPSED_STRING) {
                return literal_value($candidate);
            }
        }
    }
    fail_closed("required legacy variable is missing");
}

function call_string_arguments(array $tokens, string $function, int $required): array
{
    $count = count($tokens);
    for ($index = 0; $index < $count; $index++) {
        $token = $tokens[$index];
        if (!is_array($token) || $token[0] !== T_STRING || strcasecmp($token[1], $function) !== 0) {
            continue;
        }
        $arguments = [];
        $depth = 0;
        $opened = false;
        for ($cursor = $index + 1; $cursor < $count; $cursor++) {
            $candidate = $tokens[$cursor];
            if ($candidate === '(') {
                $depth++;
                $opened = true;
                continue;
            }
            if (!$opened) {
                if (is_array($candidate) && in_array($candidate[0], [T_WHITESPACE, T_NS_SEPARATOR], true)) {
                    continue;
                }
                break;
            }
            if ($candidate === ')') {
                $depth--;
                if ($depth === 0) {
                    break;
                }
                continue;
            }
            if ($depth === 1 && is_array($candidate) && $candidate[0] === T_CONSTANT_ENCAPSED_STRING) {
                $arguments[] = literal_value($candidate);
            }
        }
        if (count($arguments) >= $required) {
            return array_slice($arguments, 0, $required);
        }
    }
    fail_closed("required legacy function call is missing");
}

function write_secret(string $directory, string $name, string $value): void
{
    if ($value === '' || str_contains($value, "\0") || str_contains($value, "\n") || str_contains($value, "\r")) {
        fail_closed("legacy secret value failed validation");
    }
    $path = $directory . DIRECTORY_SEPARATOR . $name;
    if (file_exists($path)) {
        fail_closed("refusing to overwrite an existing secret file");
    }
    if (@file_put_contents($path, $value . "\n", LOCK_EX) === false || !@chmod($path, 0400)) {
        fail_closed("cannot install extracted secret file");
    }
}

if (PHP_SAPI !== 'cli' || !function_exists('posix_geteuid') || posix_geteuid() !== 0 || $argc !== 2) {
    fail_closed("run as root with one explicit staging directory");
}

$outputDirectory = $argv[1];
$resolvedDirectory = realpath($outputDirectory);
if ($resolvedDirectory === false || $resolvedDirectory !== $outputDirectory || !is_dir($resolvedDirectory)) {
    fail_closed("staging directory must be an existing absolute path without symlinks");
}

$withingsTokens = tokens('/srv/cron/get_withings.php');
$telegramTokens = tokens('/srv/cron/send_telergam.php');

[$dbHost, $dbUser, $dbPassword] = call_string_arguments($withingsTokens, 'mysqli_connect', 3);
$clientId = array_string_value($withingsTokens, 'client_id');
$clientSecret = array_string_value($withingsTokens, 'client_secret');
$telegramToken = variable_string_value($telegramTokens, '$token');
[$telegramChatId] = call_string_arguments($withingsTokens, 't_send_messages', 1);

mysqli_report(MYSQLI_REPORT_OFF);
$database = @new mysqli($dbHost, $dbUser, $dbPassword, 'amigo');
if ($database->connect_errno !== 0) {
    fail_closed("cannot read the legacy integration database");
}
$result = @$database->query('SELECT access_token, refresh_token FROM amigo.seting');
if ($result === false) {
    fail_closed("cannot read the legacy Withings token row");
}
if ($result->num_rows !== 1) {
    $database->close();
    fail_closed("legacy Withings token table must contain exactly one row");
}
$row = $result->fetch_assoc();
$database->close();
if (!is_array($row)) {
    fail_closed("legacy Withings token row is empty");
}

write_secret($resolvedDirectory, 'withings_client_id', $clientId);
write_secret($resolvedDirectory, 'withings_client_secret', $clientSecret);
write_secret($resolvedDirectory, 'withings_access_token', (string) ($row['access_token'] ?? ''));
write_secret($resolvedDirectory, 'withings_refresh_token', (string) ($row['refresh_token'] ?? ''));
write_secret($resolvedDirectory, 'telegram_bot_token', $telegramToken);
write_secret($resolvedDirectory, 'telegram_chat_id', $telegramChatId);

fwrite(STDERR, "Legacy integration credentials were migrated into root-only secret files.\n");
