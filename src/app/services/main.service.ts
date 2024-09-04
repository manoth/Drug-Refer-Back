import { Inject, Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { JwtHelperService } from '@auth0/angular-jwt';

@Injectable({
  providedIn: 'root'
})
export class MainService {

  private jwtHelper = new JwtHelperService();

  constructor(
    @Inject('APIURL') private apiUrl: string,
    @Inject('ACCESSTOKEN') private accessTokenName: string,
    private http: HttpClient,
    private router: Router
  ) { }

  get(path: string) {
    const url: string = `${this.apiUrl}/${path}`;
    return this.http.get(url).toPromise();
  }

  post(path: string, data: any) {
    const url: string = `${this.apiUrl}/${path}`;
    return this.http.post(url, data).toPromise();
  }

  put(path: string, data: any) {
    const url: string = `${this.apiUrl}/${path}`;
    return this.http.put(url, data).toPromise();
  }

  delete(path: string, data: any) {
    const url: string = `${this.apiUrl}/${path}`;
    return this.http.delete(url, data).toPromise();
  }

  logout() {
    localStorage.removeItem(this.accessTokenName);
    this.router.navigate(['/signin']);
  }

  in_array(str: any, array: Array<any>): boolean {
    return array.indexOf(str) >= 0;
  }

  public get accessToken(): any {
    return localStorage.getItem(this.accessTokenName);
  }

  public get jwtDecode(): any {
    try {
      return this.jwtHelper.decodeToken(this.accessToken);
    } catch (err) {
      return false;
    }
  }

  public get auth1(): boolean {
    try {
      return !this.jwtHelper.isTokenExpired(this.accessToken);
    } catch (err) {
      this.logout();
      return true;
    }
  }

  public get auth2(): boolean {
    const d = this.jwtDecode;
    return (d.user_level) ? d.user_level > 1 : false;
  }

}
