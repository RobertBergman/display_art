#define STB_IMAGE_IMPLEMENTATION
#include "stb_image.h"
#include <drm_fourcc.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <xf86drm.h>
#include <xf86drmMode.h>

typedef struct {
  int fd;
  uint32_t fb_id;
  uint32_t width;
  uint32_t height;
  uint32_t pitch;
  uint32_t handle;
  uint64_t size;
  void *map;
} FB;

typedef struct {
  drmModeConnector *connector;
  drmModeModeInfo *mode;
  uint32_t crtc_id;
  uint32_t encoder_id;
} Display;

static volatile sig_atomic_t running = 1;
static int g_fd = -1;
static uint32_t g_crtc_id = 0;

static void sighandler(int sig) {
  (void)sig;
  if (g_fd >= 0 && g_crtc_id)
    drmModeSetCrtc(g_fd, g_crtc_id, 0, 0, 0, NULL, 0, NULL);
  if (g_fd >= 0)
    drmDropMaster(g_fd);
  running = 0;
}

static int find_display(int fd, drmModeRes *res, Display *d) {
  for (int i = 0; i < res->count_connectors; i++) {
    drmModeConnector *conn = drmModeGetConnector(fd, res->connectors[i]);
    if (!conn)
      continue;
    if (conn->connection == DRM_MODE_CONNECTED && conn->count_modes > 0) {
      d->connector = conn;
      d->mode = &conn->modes[0];

      for (int j = 0; j < conn->count_encoders; j++) {
        drmModeEncoder *enc = drmModeGetEncoder(fd, conn->encoders[j]);
        if (!enc)
          continue;
        if (enc->crtc_id) {
          d->crtc_id = enc->crtc_id;
          d->encoder_id = enc->encoder_id;
          drmModeFreeEncoder(enc);
          return 0;
        }
        for (int k = 0; k < res->count_crtcs; k++) {
          if (enc->possible_crtcs & (1 << k)) {
            d->crtc_id = res->crtcs[k];
            d->encoder_id = enc->encoder_id;
            drmModeFreeEncoder(enc);
            return 0;
          }
        }
        drmModeFreeEncoder(enc);
      }

      for (int j = 0; j < res->count_encoders; j++) {
        drmModeEncoder *enc = drmModeGetEncoder(fd, res->encoders[j]);
        if (!enc)
          continue;
        for (int k = 0; k < res->count_crtcs; k++) {
          if (enc->possible_crtcs & (1 << k)) {
            d->crtc_id = res->crtcs[k];
            d->encoder_id = enc->encoder_id;
            drmModeFreeEncoder(enc);
            return 0;
          }
        }
        drmModeFreeEncoder(enc);
      }
      drmModeFreeConnector(conn);
      d->connector = NULL;
    } else {
      drmModeFreeConnector(conn);
    }
  }
  return -1;
}

static int create_fb(int fd, uint32_t w, uint32_t h, FB *fb) {
  struct drm_mode_create_dumb creq = {0};
  creq.width = w;
  creq.height = h;
  creq.bpp = 32;
  if (drmIoctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &creq) < 0) {
    perror("CREATE_DUMB");
    return -1;
  }
  fb->handle = creq.handle;
  fb->pitch = creq.pitch;
  fb->size = creq.size;

  struct drm_mode_map_dumb mreq = {0};
  mreq.handle = creq.handle;
  if (drmIoctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &mreq) < 0) {
    perror("MAP_DUMB");
    return -1;
  }

  fb->map = mmap(0, creq.size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, mreq.offset);
  if (fb->map == MAP_FAILED) {
    perror("mmap");
    return -1;
  }

  uint32_t handles[4] = {creq.handle, 0, 0, 0};
  uint32_t pitches[4] = {creq.pitch, 0, 0, 0};
  uint32_t offsets[4] = {0, 0, 0, 0};
  uint32_t fb_id;
  if (drmModeAddFB2(fd, w, h, DRM_FORMAT_XRGB8888, handles, pitches, offsets, &fb_id, 0) < 0) {
    if (drmModeAddFB2(fd, w, h, DRM_FORMAT_ARGB8888, handles, pitches, offsets, &fb_id, 0) < 0) {
      fprintf(stderr, "Warning: using XRGB2101010 fallback\n");
      if (drmModeAddFB2(fd, w, h, DRM_FORMAT_XRGB2101010, handles, pitches, offsets, &fb_id, 0) <
          0) {
        perror("ADDFB2");
        return -1;
      }
    }
  }

  fb->fd = fd;
  fb->width = w;
  fb->height = h;
  fb->fb_id = fb_id;
  return 0;
}

static void destroy_fb(FB *fb) {
  drmModeRmFB(fb->fd, fb->fb_id);
  munmap(fb->map, fb->size);
  struct drm_mode_destroy_dumb dreq = {0};
  dreq.handle = fb->handle;
  drmIoctl(fb->fd, DRM_IOCTL_MODE_DESTROY_DUMB, &dreq);
}

int main(int argc, char **argv) {
  if (argc < 2) {
    fprintf(stderr, "Usage: %s <image_file> [card_device]\n", argv[0]);
    return 1;
  }

  const char *img_path = argv[1];
  const char *card = argc > 2 ? argv[2] : "/dev/dri/card1";

  int w_img, h_img, channels;
  unsigned char *img_data =
      stbi_load(img_path, &w_img, &h_img, &channels, 4);
  if (!img_data) {
    fprintf(stderr, "Failed to load image: %s\n", img_path);
    return 1;
  }

  int fd = open(card, O_RDWR);
  if (fd < 0) {
    perror("open");
    stbi_image_free(img_data);
    return 1;
  }

  drmModeRes *res = drmModeGetResources(fd);
  if (!res) {
    perror("drmModeGetResources");
    stbi_image_free(img_data);
    close(fd);
    return 1;
  }

  Display d = {0};
  if (find_display(fd, res, &d) < 0) {
    fprintf(stderr, "No connected display found\n");
    drmModeFreeResources(res);
    stbi_image_free(img_data);
    close(fd);
    return 1;
  }

  uint32_t sw = d.mode->hdisplay;
  uint32_t sh = d.mode->vdisplay;

  FB fb = {0};
  if (create_fb(fd, sw, sh, &fb) < 0) {
    fprintf(stderr, "Failed to create framebuffer\n");
    drmModeFreeResources(res);
    drmModeFreeConnector(d.connector);
    stbi_image_free(img_data);
    close(fd);
    return 1;
  }

  memset(fb.map, 0, fb.size);
  uint32_t *pixels = (uint32_t *)fb.map;
  int fb_stride = fb.pitch / 4;

  float scale = (float)w_img / h_img;
  float screen_scale = (float)sw / sh;
  int dw, dh, ox, oy;

  if (scale > screen_scale) {
    dw = sw;
    dh = (int)(sw / scale);
    ox = 0;
    oy = (sh - dh) / 2;
  } else {
    dh = sh;
    dw = (int)(sh * scale);
    ox = (sw - dw) / 2;
    oy = 0;
  }

  for (int y = 0; y < dh; y++) {
    int sy = (int)((float)y / dh * h_img);
    if (sy < 0)
      sy = 0;
    if (sy >= h_img)
      sy = h_img - 1;
    uint32_t *row = pixels + (oy + y) * fb_stride + ox;
    unsigned char *src_row = img_data + sy * w_img * 4;
    for (int x = 0; x < dw; x++) {
      int sx = (int)((float)x / dw * w_img);
      if (sx < 0)
        sx = 0;
      if (sx >= w_img)
        sx = w_img - 1;
      unsigned char *psrc = src_row + sx * 4;
      row[x] = (psrc[0] << 16) | (psrc[1] << 8) | psrc[2] | (psrc[3] << 24);
    }
  }

  printf("Displaying %s (%dx%d) on %dx%d screen\n", img_path, w_img, h_img, sw, sh);

  drmSetMaster(fd);

  if (drmModeSetCrtc(fd, d.crtc_id, fb.fb_id, 0, 0, &d.connector->connector_id, 1, d.mode) < 0) {
    perror("drmModeSetCrtc");
    destroy_fb(&fb);
    drmModeFreeConnector(d.connector);
    drmModeFreeResources(res);
    stbi_image_free(img_data);
    close(fd);
    return 1;
  }

  g_fd = fd;
  g_crtc_id = d.crtc_id;
  signal(SIGTERM, sighandler);
  signal(SIGINT, sighandler);

  printf("READY\n");
  fflush(stdout);

  while (running) {
    pause();
  }

  destroy_fb(&fb);
  drmDropMaster(fd);
  drmModeFreeConnector(d.connector);
  drmModeFreeResources(res);
  stbi_image_free(img_data);
  close(fd);
  return 0;
}
