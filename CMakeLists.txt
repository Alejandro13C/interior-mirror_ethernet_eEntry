cmake_minimum_required(VERSION 3.10)
project(interior_mirror C)
set(CMAKE_C_STANDARD 11)
set(CMAKE_BUILD_TYPE Release)

add_executable(interior_mirror src/main.c)
target_include_directories(interior_mirror PRIVATE include)
