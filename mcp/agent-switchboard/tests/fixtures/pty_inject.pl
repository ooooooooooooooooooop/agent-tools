#!/usr/bin/env perl

use strict;
use warnings;
use Fcntl qw(O_RDWR);

my ($tty, $text) = @ARGV;
die "usage: pty_inject.pl TTY TEXT\n" unless defined $tty && defined $text;
sysopen(my $handle, "/dev/$tty", O_RDWR) or die "open /dev/$tty: $!\n";
for my $byte (split //, "$text\n") {
    ioctl($handle, 0x5412, $byte) or die "TIOCSTI /dev/$tty: $!\n";
}
