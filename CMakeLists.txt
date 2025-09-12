# === GStreamer rtp_viewer ===
find_package(PkgConfig REQUIRED)
pkg_check_modules(GST REQUIRED
  gstreamer-1.0
  gstreamer-app-1.0
  gstreamer-rtp-1.0
  gstreamer-video-1.0
)

add_executable(rtp_viewer
  ${CMAKE_SOURCE_DIR}/src/apps/rtp_viewer.c
)

target_include_directories(rtp_viewer PRIVATE
  ${GST_INCLUDE_DIRS}
  ${CMAKE_SOURCE_DIR}/include
)
target_link_libraries(rtp_viewer PRIVATE
  ${GST_LIBRARIES}
)
target_compile_options(rtp_viewer PRIVATE
  ${GST_CFLAGS_OTHER} -O2 -Wall -Wextra -Wpedantic
)
