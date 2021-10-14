#!/bin/sh

for arg do
  shift
  case $arg in
    (-lgcc_s) : ;;
    (*) set -- "$@" "$arg" ;;
  esac
done

exec gcc "$@" -lgcc_eh -static-libstdc++
